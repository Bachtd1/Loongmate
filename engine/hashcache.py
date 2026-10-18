"""
Cache SHA-256 giữa các lần quét cho LoongMate (dedupe trên máy mất ~260 giây vì băm lại ~9,7 GB mỗi lần).

API
---
HashCache(app_data)                 sqlite3 tại <app_data>/cache/hashes.sqlite (tạo khi cần)
  .get(path) -> Optional[str]       hash đã lưu nếu (đường dẫn tuyệt đối, st_size, st_mtime_ns) còn khớp
                                    VÀ đủ tin cậy (xem dưới); ngược lại None
  .put(path, sha256) -> None        lưu hash cho trạng thái file HIỆN TẠI
  .hash(path, compute) -> str       get(), không có thì compute(path) rồi lưu. Không lưu nếu file đổi
                                    (size/mtime) trong lúc băm.
  .prune_missing() -> int           xoá các dòng mà file không còn tồn tại; trả số dòng đã xoá
  .hits / .misses                   đếm get() thành công / thất bại của hash()
  .flush() / .close()   hash() gom ~50 dòng mỗi lần commit; gọi flush()/close() khi xong

Khi nào KHÔNG tin cache (cửa sổ 2 giây của FAT32)
-------------------------------------------------
mtime FAT32 chỉ chính xác 2 giây. Một file bị sửa trong cùng cửa sổ 2 giây với lần băm trước, cùng kích
thước, sẽ có đúng mtime cũ -> khoá khớp nhưng nội dung đã khác (SaveVault từng ghi hash cũ cho save cùng
kích thước sửa cùng giây). Vì vậy mỗi dòng lưu `hashed_at` (thời điểm BẮT ĐẦU băm, đồng hồ tường) và
get() chỉ tin cache khi `mtime` cũ hơn `hashed_at` ít nhất 2 giây. File vừa sửa/vừa ghi (hoặc có mtime
ở tương lai so với đồng hồ máy) thì luôn bị băm lại; sau lần băm lại ở thời điểm đủ muộn thì cache có tác dụng.

Cache chỉ dùng để TÌM ỨNG VIÊN (find_duplicates). Xoá file (dedupe.execute_deletion) luôn băm mới, không
bao giờ đọc cache: cache hỏng/nói dối chỉ làm đề xuất sai chứ không làm xoá nhầm.

Cache lỗi (thư mục chỉ đọc, sqlite hỏng) -> hoạt động như không có cache, không làm hỏng lần quét.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

FAT_MTIME_SLACK_NS = 2_000_000_000   # mtime FAT32 chính xác 2 giây
CACHE_NAME = "hashes.sqlite"

_SCHEMA = ("CREATE TABLE IF NOT EXISTS hashes ("
           "path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, "
           "sha256 TEXT NOT NULL, hashed_at_ns INTEGER NOT NULL)")


def _abs(path: Any) -> str:
    return os.path.abspath(str(path))


class HashCache:
    def __init__(self, app_data: Any):
        self.db_path = Path(app_data) / "cache" / CACHE_NAME
        self.hits = 0
        self.misses = 0
        self._db: Optional[sqlite3.Connection] = None
        self._failed = False
        self._pending = 0

    # ------------------------------------------------------------ nội bộ
    def _conn(self) -> Optional[sqlite3.Connection]:
        if self._db is not None:
            return self._db
        if self._failed:
            return None
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.db_path))
            db.execute(_SCHEMA)
            db.commit()
            self._db = db
        except (sqlite3.Error, OSError):
            self._failed = True
        return self._db

    def _store(self, path: str, sha256: str, size: int, mtime_ns: int, hashed_at_ns: int,
               commit: bool = True) -> None:
        db = self._conn()
        if db is None:
            return
        try:
            db.execute("INSERT OR REPLACE INTO hashes(path,size,mtime_ns,sha256,hashed_at_ns) VALUES(?,?,?,?,?)",
                       (path, size, mtime_ns, sha256, hashed_at_ns))
            self._pending += 1
            if commit or self._pending >= 50:
                db.commit()
                self._pending = 0
        except sqlite3.Error:
            pass

    def flush(self) -> None:
        """Ghi xuống đĩa các dòng đang chờ (hash() gom ~50 dòng mỗi lần commit để đỡ fsync trên thẻ SD)."""
        if self._db is not None and self._pending:
            try:
                self._db.commit()
            except sqlite3.Error:
                pass
            self._pending = 0

    # ------------------------------------------------------------ API
    def get(self, path: Any) -> Optional[str]:
        db = self._conn()
        if db is None:
            return None
        key = _abs(path)
        try:
            st = os.stat(key)
            row = db.execute("SELECT size,mtime_ns,sha256,hashed_at_ns FROM hashes WHERE path=?", (key,)).fetchone()
        except (OSError, sqlite3.Error):
            return None
        if row is None:
            return None
        size, mtime_ns, sha, hashed_at_ns = row
        if st.st_size != size or st.st_mtime_ns != mtime_ns:
            return None
        if mtime_ns > hashed_at_ns - FAT_MTIME_SLACK_NS:
            return None   # file có thể đã đổi trong cùng cửa sổ 2 giây với lần băm -> không tin
        return sha

    def put(self, path: Any, sha256: str) -> None:
        key = _abs(path)
        try:
            now_ns = time.time_ns()
            st = os.stat(key)
        except OSError:
            return
        self._store(key, sha256, st.st_size, st.st_mtime_ns, now_ns)

    def hash(self, path: Any, compute: Callable[[Path], str]) -> str:
        cached = self.get(path)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        key = _abs(path)
        started_ns = time.time_ns()        # trước khi băm: bảo thủ hơn thời điểm kết thúc
        try:
            before = os.stat(key)
        except OSError:
            before = None
        sha = compute(Path(path))
        if before is not None:
            try:
                after = os.stat(key)
            except OSError:
                after = None
            if after is not None and (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns):
                self._store(key, sha, before.st_size, before.st_mtime_ns, started_ns, commit=False)
        return sha

    def prune_missing(self) -> int:
        db = self._conn()
        if db is None:
            return 0
        try:
            paths = [r[0] for r in db.execute("SELECT path FROM hashes")]
            gone = [(p,) for p in paths if not os.path.exists(p)]
            if gone:
                db.executemany("DELETE FROM hashes WHERE path=?", gone)
                db.commit()
            return len(gone)
        except sqlite3.Error:
            return 0

    def close(self) -> None:
        self.flush()
        if self._db is not None:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
