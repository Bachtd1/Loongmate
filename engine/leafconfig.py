"""Sao lưu cấu hình Leaf / RetroArch ("cấu hình chơi"), so sánh và khôi phục theo nhóm.

SaveVault giữ save/state; module này giữ phần người dùng đã chỉnh: retroarch.cfg chung, override/core option/remap theo
core và game, cấu hình emulator độc lập, và cơ sở dữ liệu của Leaf (chỉ sao lưu).

Nguồn (đọc từ mã Jawaka, chạy thật trên máy 2026-10-17), tính từ gốc thẻ Leaf <sd>:
  .umrk/<p>/retroarch/retroarch.cfg                       cấu hình RetroArch chung; Leaf ghép với defaults mỗi lần mở game
                                                          và ghi ngược lại khi thoát (bỏ ~70 khoá Leaf tự quản)
  .umrk/<p>/retroarch/.config/retroarch/config/**         override (.cfg), core option (.opt), remap (.rmp)
  .userdata/<p>/mupen64plus/config/*                      emulator độc lập (N64)
  .umrk/<p>/library.db, services-control.db               cài đặt Leaf, core/hiệu năng theo game, cờ "Start with Leaf"
Cập nhật Leaf giữ nguyên .umrk/ và .userdata/ (theo tài liệu và script cài của Leaf) nhưng thay core và defaults; bản sao lưu
trước và sau cập nhật cho biết chính xác cấu hình nào đã đổi.

KHÔNG sao lưu wifi.conf (chứa mật khẩu Wi-Fi) và shader đi kèm Leaf (Leaf tự cài lại).
KHÔNG khôi phục library.db / services-control.db: Leaf luôn mở hai file này khi LoongMate chạy, ghi đè có thể làm hỏng.
Khôi phục: từ chối khi đang có game; tự sao lưu hiện trạng trước; ghi file tạm rồi os.replace; không xoá file nào.

Lưu ở <app_data>/ConfigVault/: objects/<sha256[:2]>/<sha256> (lưu theo nội dung: file không đổi giữa các bản chỉ tốn một lần)
và snapshots/<id>/manifest.json."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = 1
KEEP = 15                                   # số bản giữ lại (bản cũ hơn bị xoá, object không còn ai dùng bị dọn)
CONFIG_EXTS = (".cfg", ".opt", ".rmp", ".ini")
MAX_FILE = 8 * 1024 * 1024                  # file cấu hình lớn bất thường thì bỏ qua (không phải cấu hình)
RESTORABLE = ("retroarch", "overrides", "standalone")
GROUP_VI = {"retroarch": "RetroArch chung (retroarch.cfg)", "overrides": "Override / core option / remap theo core-game",
            "standalone": "Emulator độc lập (Mupen64Plus)", "leaf_db": "Cơ sở dữ liệu Leaf (chỉ sao lưu)"}
_ID_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
_KV_RE = re.compile(r'^\s*([A-Za-z0-9_.\-]+)\s*=\s*"?(.*?)"?\s*$')


class ConfigError(Exception):
    pass


def _vault(app_data) -> Path:
    return Path(app_data) / "ConfigVault"


def leaf_version(sd, platform: str) -> Optional[str]:
    try:
        v = json.loads((Path(sd) / ".umrk" / platform / "release.json").read_text(encoding="utf-8")).get("version")
        return v if isinstance(v, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def collect(sd, platform: str) -> Dict[str, Tuple[Path, str]]:
    """{đường dẫn tương đối từ gốc thẻ Leaf: (đường dẫn thật, nhóm)} của mọi file cấu hình hiện có."""
    sd = Path(sd)
    umrk = sd / ".umrk" / platform
    out: Dict[str, Tuple[Path, str]] = {}

    def add(p: Path, group: str) -> None:
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_FILE:
                out[p.relative_to(sd).as_posix()] = (p, group)
        except OSError:
            pass

    def add_tree(d: Path, group: str) -> None:
        for p in sorted(d.rglob("*")) if d.is_dir() else ():
            if p.suffix.lower() in CONFIG_EXTS:
                add(p, group)

    add(umrk / "retroarch" / "retroarch.cfg", "retroarch")
    add_tree(umrk / "retroarch" / ".config" / "retroarch" / "config", "overrides")
    add_tree(sd / ".userdata" / platform / "mupen64plus" / "config", "standalone")
    for name in ("library.db", "services-control.db"):
        add(umrk / name, "leaf_db")
    return out


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_atomic(dst: Path, src: Optional[Path] = None, data: Optional[bytes] = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + dst.name + ".", suffix=".tmp", dir=str(dst.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            if src is not None:
                with open(src, "rb") as s:
                    shutil.copyfileobj(s, f)
            else:
                f.write(data or b"")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(dst.parent)


def _db_copy(src: Path, dst: Path) -> None:
    """Bản sao NHẤT QUÁN của SQLite đang được Leaf mở (backup API); không mở được thì chép thường."""
    try:
        s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            d = sqlite3.connect(str(dst))
            try:
                s.backup(d)
            finally:
                d.close()
        finally:
            s.close()
    except sqlite3.Error:
        shutil.copyfile(src, dst)


def _store(vault: Path, p: Path, group: str) -> Tuple[str, int]:
    """Đưa file vào kho object; trả (sha256, size)."""
    work = vault / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    src = p
    tmpdb = None
    if group == "leaf_db":
        tmpdb = work / ("db-" + str(os.getpid()))
        _db_copy(p, tmpdb)
        src = tmpdb
    try:
        sha, size = _sha(src), src.stat().st_size
        obj = vault / "objects" / sha[:2] / sha
        if not obj.exists():
            _write_atomic(obj, src=src)
        return sha, size
    finally:
        if tmpdb is not None:
            try:
                tmpdb.unlink()
            except OSError:
                pass


def list_snapshots(app_data) -> List[Dict[str, Any]]:
    out = []
    base = _vault(app_data) / "snapshots"
    for d in sorted(base.iterdir(), reverse=True) if base.is_dir() else ():
        try:
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("schema") == SCHEMA and _ID_RE.match(d.name):
            out.append({"id": d.name, "created_at": m.get("created_at"), "label": m.get("label", ""),
                        "leaf_version": m.get("leaf_version"), "files": len(m.get("files", {})),
                        "bytes": sum(f.get("size", 0) for f in m.get("files", {}).values())})
    return out


def _manifest(app_data, snap_id: str) -> Dict[str, Any]:
    if not _ID_RE.match(snap_id or ""):
        raise ConfigError(f"ID bản sao lưu không hợp lệ: '{snap_id}'.")
    try:
        m = json.loads((_vault(app_data) / "snapshots" / snap_id / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigError(f"Không có bản sao lưu cấu hình '{snap_id}'.")
    if not isinstance(m, dict) or m.get("schema") != SCHEMA:
        raise ConfigError(f"Bản sao lưu '{snap_id}' thuộc phiên bản LoongMate khác (schema != {SCHEMA}).")
    return m


def snapshot(app_data, sd, platform: str, label: str = "manual", force: bool = False, keep: int = KEEP) -> Dict[str, Any]:
    """Sao lưu cấu hình hiện tại. Không đổi gì so với bản gần nhất (và không force) thì không tạo bản mới."""
    files = collect(sd, platform)
    if not files:
        return {"success": False, "created": False, "message": f"Không thấy cấu hình Leaf nào trong {sd} (thẻ Leaf?)."}
    vault = _vault(app_data)
    entries: Dict[str, Dict[str, Any]] = {}
    for rel, (p, group) in files.items():
        try:
            sha, size = _store(vault, p, group)
        except OSError:
            continue                                  # file vừa biến mất / không đọc được: bỏ qua, các file khác vẫn lưu
        entries[rel] = {"sha256": sha, "size": size, "group": group}
    ver = leaf_version(sd, platform)
    prev = list_snapshots(app_data)
    if prev and not force:
        last = _manifest(app_data, prev[0]["id"])
        if {k: v["sha256"] for k, v in last.get("files", {}).items()} == {k: v["sha256"] for k, v in entries.items()} \
                and last.get("leaf_version") == ver:
            return {"success": True, "created": False, "id": prev[0]["id"],
                    "message": f"Cấu hình không đổi so với bản {prev[0]['id']}: không tạo bản mới."}
    sid = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while (vault / "snapshots" / sid).exists() or any(s["id"] == sid for s in prev):
        n += 1
        sid = time.strftime("%Y%m%d-%H%M%S") + f"-{n}"
    man = {"schema": SCHEMA, "id": sid, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "label": label,
           "leaf_version": ver, "platform": platform, "files": entries}
    _write_atomic(vault / "snapshots" / sid / "manifest.json", data=json.dumps(man, ensure_ascii=False, indent=1).encode("utf-8"))
    pruned = _prune(app_data, keep)
    by = {}
    for v in entries.values():
        by[v["group"]] = by.get(v["group"], 0) + 1
    return {"success": True, "created": True, "id": sid, "leaf_version": ver, "files": len(entries), "groups": by, "pruned": pruned,
            "message": f"Đã sao lưu {len(entries)} file cấu hình (Leaf {ver or '?'}) vào bản {sid}."}


def _prune(app_data, keep: int) -> List[str]:
    snaps = list_snapshots(app_data)
    vault = _vault(app_data)
    gone = []
    for s in snaps[max(keep, 1):]:
        shutil.rmtree(vault / "snapshots" / s["id"], ignore_errors=True)
        gone.append(s["id"])
    if gone:
        used = set()
        for s in list_snapshots(app_data):
            used |= {f["sha256"] for f in _manifest(app_data, s["id"]).get("files", {}).values()}
        for obj in (vault / "objects").rglob("*") if (vault / "objects").is_dir() else ():
            if obj.is_file() and obj.name not in used:
                try:
                    obj.unlink()
                except OSError:
                    pass
    return gone


def _kv(data: bytes) -> Dict[str, str]:
    out = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        m = _KV_RE.match(line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2)
    return out


def _obj(app_data, sha: str) -> Path:
    return _vault(app_data) / "objects" / sha[:2] / sha


def diff(app_data, sd, platform: str, snap_id: str, max_keys: int = 60) -> Dict[str, Any]:
    """So bản sao lưu với cấu hình HIỆN TẠI: file thêm/mất/đổi; với file key=value (.cfg/.opt/.rmp/.ini) liệt kê khoá đổi."""
    man = _manifest(app_data, snap_id)
    old = man.get("files", {})
    cur = collect(sd, platform)
    changed, added, removed = [], [], []
    for rel in sorted(set(old) | set(cur)):
        if rel not in cur:
            removed.append({"path": rel, "group": old[rel]["group"]})
            continue
        p, group = cur[rel]
        if rel not in old:
            added.append({"path": rel, "group": group})
            continue
        if group == "leaf_db":
            continue                                  # DB đổi liên tục (lịch sử chơi): không so
        try:
            if _sha(p) == old[rel]["sha256"]:
                continue
            new_b = p.read_bytes()
            old_b = _obj(app_data, old[rel]["sha256"]).read_bytes()
        except OSError:
            continue
        keys = []
        if p.suffix.lower() in CONFIG_EXTS:
            a, b = _kv(old_b), _kv(new_b)
            for k in sorted(set(a) | set(b)):
                if a.get(k) != b.get(k):
                    keys.append({"key": k, "old": a.get(k), "new": b.get(k)})
        changed.append({"path": rel, "group": group, "keys": keys[:max_keys], "more_keys": max(0, len(keys) - max_keys)})
    return {"success": True, "id": snap_id, "snapshot_leaf_version": man.get("leaf_version"),
            "current_leaf_version": leaf_version(sd, platform), "changed": changed, "added": added, "removed": removed,
            "message": (f"So với bản {snap_id}: {len(changed)} file đổi, {len(added)} file mới, {len(removed)} file không còn."
                        if changed or added or removed else f"Cấu hình hiện tại giống hệt bản {snap_id}.")}


def restore(app_data, sd, platform: str, snap_id: str, groups: List[str], running=None) -> Dict[str, Any]:
    """Ghi lại các file thuộc `groups` từ bản sao lưu. Không xoá file nào; DB của Leaf không bao giờ ghi."""
    bad = [g for g in groups if g not in RESTORABLE]
    if not groups or bad:
        raise ConfigError("Nhóm khôi phục được: " + ", ".join(RESTORABLE) + (f" (không nhận: {', '.join(bad)})" if bad else ""))
    if running is None:
        from .savevault import running_emulators
        running = running_emulators
    emus = running()
    if emus:
        return {"success": False, "error_kind": "game_running", "running": emus,
                "message": f"Đang có game chạy ({', '.join(emus)}): Leaf sẽ ghi đè cấu hình khi thoát game. Thoát game rồi thử lại. Chưa ghi gì."}
    man = _manifest(app_data, snap_id)
    todo = {rel: f for rel, f in man.get("files", {}).items() if f.get("group") in groups}
    sd = Path(sd).resolve()
    for rel in todo:
        dst = (sd / rel).resolve()
        if ".." in Path(rel).parts or not str(dst).startswith(str(sd) + os.sep):
            raise ConfigError(f"Đường dẫn lạ trong bản sao lưu: {rel}")
        if not _obj(app_data, todo[rel]["sha256"]).is_file():
            raise ConfigError(f"Bản sao lưu thiếu dữ liệu cho {rel}: không khôi phục gì.")
    before = snapshot(app_data, sd, platform, label=f"trước khi khôi phục {snap_id}", force=True)
    restored, same = [], []
    for rel, f in sorted(todo.items()):
        dst = sd / rel
        try:
            if dst.is_file() and _sha(dst) == f["sha256"]:
                same.append(rel)
                continue
        except OSError:
            pass
        _write_atomic(dst, src=_obj(app_data, f["sha256"]))
        restored.append(rel)
    return {"success": True, "id": snap_id, "groups": groups, "restored": restored, "unchanged": len(same),
            "backup_before": before.get("id"),
            "message": f"Đã khôi phục {len(restored)} file ({', '.join(GROUP_VI[g] for g in groups)}); {len(same)} file vốn đã giống. "
                       f"Bản trước khi khôi phục: {before.get('id')}. Có hiệu lực từ lần mở game kế tiếp."}
