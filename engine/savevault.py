"""
SaveVault module for LoongMate.
Handles manifest-driven incremental backups, atomic snapshot commits,
safe crash-resilient restores with pre-restore snapshots, and conflict preservation.
"""

import os
import re
import json
import time
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional
from .inventory import compute_sha256, is_safe_relative_path
from .autobackup import ProcWatcher


def running_emulators() -> List[str]:
    """Tên tiến trình emulator đang chạy (/proc/*/comm, cùng danh sách AutoBackup dùng). Rỗng trên PC/khi không đọc được /proc."""
    return sorted(set(ProcWatcher().scan().values()))


def _busy_refusal(action: str) -> Optional[Dict[str, Any]]:
    """Emulator đang chạy thì nó còn giữ save trong RAM và sẽ ghi đè file vừa khôi phục khi thoát -> từ chối."""
    emus = running_emulators()
    if not emus:
        return None
    return {"success": False, "error_kind": "game_running", "running": emus,
            "message": f"Đang có game chạy ({', '.join(emus)}): {action} lúc này sẽ bị emulator ghi đè khi thoát game. "
                       f"Thoát game (Save & Quit) rồi thử lại. Chưa ghi gì."}

# Only safe filesystem characters survive in a user-supplied snapshot label (FAT32-safe, no path separators).
_LABEL_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")

# snapshot_id = snap_<YYYYMMDD>_<HHMMSS>_<label>[_<n>]  (_<n> = hậu tố chống trùng trong cùng giây).
_SNAP_ID_LABEL_RE = re.compile(r"^snap_\d{8}_\d{6}_(.+)$")

# Nhãn do agent tự động tạo; nhóm này có hạn mức giữ riêng (auto_retention).
AUTO_LABEL_PREFIX = "auto"


# Hậu tố snapshot đã cấp gần nhất theo thư mục: {str(snapshots_dir): (base_id, suffix)}; xem SaveVault._next_suffix.
_LAST_SUFFIX: Dict[str, tuple] = {}

_SNAP_ORDER_RE = re.compile(r"^snap_(\d{8}_\d{6})_(.*?)(?:_(\d+))?$")


def _snapshot_sort_key(path: Path) -> tuple:
    """Khoá sắp xếp snapshot: (thời điểm, hậu tố chống trùng SỐ, tên). Sắp theo chuỗi thì
    "..._auto_10" đứng trước "..._auto_2" nên snapshot mới nhất bị coi là cũ và có thể bị prune nhầm."""
    m = _SNAP_ORDER_RE.match(path.name)
    if not m:
        return ("", 0, path.name)
    return (m.group(1), int(m.group(3) or 0), path.name)


def snapshot_label(snapshot_id: str, manifest: Optional[Dict[str, Any]] = None) -> str:
    """Nhãn của snapshot: lấy từ manifest["label"] nếu có, không thì suy từ hậu tố snapshot_id.
    Không suy được thì trả "" (coi như nhóm thủ công, hướng an toàn hơn)."""
    if manifest and isinstance(manifest.get("label"), str):
        return manifest["label"]
    m = _SNAP_ID_LABEL_RE.match(snapshot_id or "")
    return m.group(1) if m else ""


def is_auto_snapshot(snapshot_id: str, manifest: Optional[Dict[str, Any]] = None) -> bool:
    return snapshot_label(snapshot_id, manifest).startswith(AUTO_LABEL_PREFIX)


# ---------------------------------------------------------------- gom save/state theo game
# RetroArch: <game>.srm | <game>.state | <game>.state3 | <game>.state.auto | <game>.state3.png (ảnh chụp slot);
# mupen64plus: <game>.st0. Mọi slot/ảnh gộp về MỘT game; đuôi còn lại (.srm .ldci .fs ...) bị cắt như inventory.
_STATE_NAME_RE = re.compile(r"\.(?:state(?:\.auto|\d+)?|st\d)(?:\.png)?$", re.IGNORECASE)


def game_of(rel_path: str) -> tuple:
    """(key, name, core) của một file trong manifest. key = tên game không đuôi, chữ thường (khoá ổn định để
    gọi CLI); name = tên game giữ nguyên hoa/thường; core = thư mục ngay dưới Saves/ hoặc States/ (hoặc None).
    PPSSPP (PSP/SAVEDATA/<ID>/PARAM.SFO...): mỗi thư mục <ID> là một "game" — CHƯA KIỂM CHỨNG trên máy thật
    (máy chưa có save PSP)."""
    parts = rel_path.replace("\\", "/").split("/")
    core = parts[1] if len(parts) >= 3 and parts[0] in ("Saves", "States") else None
    low = [x.lower() for x in parts]
    if "savedata" in low[:-1] and low.index("savedata") + 1 < len(parts) - 1:
        name = parts[low.index("savedata") + 1]
    else:
        base = parts[-1]
        stripped = _STATE_NAME_RE.sub("", base)
        if stripped != base and stripped:
            name = stripped
        else:
            i = base.rfind(".")
            name = base[:i] if 0 < i < len(base) - 1 else base
    return name.lower(), name, core


# Thư mục của emulator chạy riêng KHÔNG phải save/state: không quét.
_EMU_SKIP_DIRS = frozenset({"cache", "config", "logs", "log", "screenshots", "shaders", "mappings", "thumbs"})
# Đuôi save/state đã biết của các emulator chạy riêng (flycast, mupen64plus, drastic, ppsspp, yaba).
_EMU_SAVE_EXTS = frozenset({
    ".srm", ".sav", ".state", ".eep", ".sra", ".fla", ".mpk", ".mcr", ".mcd", ".vmu",
    ".dsv", ".dss", ".ppst", ".yss", ".ss", ".savestate",
})
_EMU_STATE_NAME_RE = re.compile(r"\.st\d?$|\.state\d*$", re.IGNORECASE)  # mupen64plus .st/.st0-9


def _is_emu_save_file(name: str) -> bool:
    low = name.lower()
    ext = os.path.splitext(low)[1]
    if ext in _EMU_SAVE_EXTS or _EMU_STATE_NAME_RE.search(low):
        return True
    # Flycast ghi VMU/backup dạng .bin; chỉ nhận đúng tên đó để không vớ nhầm BIOS .bin.
    return ext == ".bin" and low.startswith(("vmu_save", "backup"))


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory so a prior rename/write into it is durable across power loss.
    This only has real effect on Linux/ext4-or-vfat mounts (the target device). On Windows,
    os.open()+os.fsync() on a directory raises PermissionError, so this is a no-op there.
    # CHƯA KIỂM CHỨNG TRÊN MÁY THẬT: chưa đo được hiệu lực fsync thư mục thật trên firmware Leaf/FAT32.
    """
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, AttributeError):
        pass


def _write_bytes_atomic(dst: Path, data: bytes) -> None:
    """Writes bytes to dst atomically: tmp write + fsync(file) + os.replace + best-effort fsync(dir)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f"{dst.name}.partial"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _write_file_atomic(src: Path, dst: Path, tmp_suffix: str = ".partial") -> None:
    """Copies src to dst atomically: tmp copy + fsync(file) + os.replace + best-effort fsync(dir).
    Preserves mtime via copystat, since build_current_manifest relies on mtime to decide
    whether a file needs re-hashing on the next snapshot.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f"{dst.name}{tmp_suffix}"
    try:
        with open(src, "rb") as fsrc, open(tmp, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


class SaveVault:
    def __init__(self, primary_path: Path, secondary_path: Optional[Path], userdata_path: Path, max_retention: int = 5,
                 auto_retention: int = 10):
        self.primary_path = primary_path.resolve()
        self.secondary_path = secondary_path.resolve() if secondary_path else None
        self.userdata_path = userdata_path.resolve()
        # max_retention áp cho nhóm thủ công (kể cả pre_restore); auto_retention áp riêng cho
        # snapshot nhãn "auto*" để agent tự động không đẩy mất bản thủ công của người dùng.
        self.max_retention = max_retention
        self.auto_retention = auto_retention

        self.vault_dir = self.userdata_path / "SaveVault"
        self.snapshots_dir = self.vault_dir / "snapshots"
        self.manifests_dir = self.vault_dir / "manifests"
        self.objects_dir = self.vault_dir / "objects"
        self.conflicts_dir = self.vault_dir / "conflicts"
        self.journal_file = self.vault_dir / "restore_journal.json"
        # Snapshot đang được khôi phục: không bao giờ prune (kể cả lúc tạo pre-restore, TRƯỚC khi có journal).
        self._restoring: set = set()

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.conflicts_dir.mkdir(parents=True, exist_ok=True)

    def discover_save_locations(self) -> List[Dict[str, Any]]:
        """Discovers standard and standalone emulator save/state locations."""
        locations = []

        # 1. Standard Leaf directories
        for card_label, card_root in [("primary", self.primary_path), ("secondary", self.secondary_path)]:
            if not card_root or not card_root.exists():
                continue
            for folder in ["Saves", "States"]:
                p = card_root / folder
                if p.exists() and p.is_dir():
                    locations.append({
                        "id": f"{card_label}_{folder.lower()}",
                        "card": card_label,
                        "category": folder,
                        "path": p,
                        "adapter": "retroarch_standard"
                    })

        # 2. Standalone emulator userdata locations
        # Đã kiểm trên máy thật: Leaf đặt dữ liệu emulator ở $USERDATA_PATH/<emu> =
        # <thẻ>/.userdata/mlp1/<emu> (thấy flycast/, mupen64plus/), không phải <thẻ>/.userdata/<emu>.
        # Đường cũ <thẻ>/.userdata/<emu> vẫn được dò để tương thích. Save thật hiện nằm ở
        # Saves/<core>/ gốc thẻ (đã quét ở bước 1); các thư mục dưới đây là phần bổ sung.
        # Trong .userdata chỉ lấy file save/state theo đuôi đã biết (xem _is_emu_save_file) và bỏ qua
        # cache/config/logs/screenshots; thư mục Saves/<emu> cũ thì lấy toàn bộ như trước.
        ud = self.primary_path / ".userdata"
        bases = [ud, ud / "mlp1"]
        try:
            if ud.is_dir():
                # <thẻ>/.userdata/<PLATFORM>/<emu>: PLATFORM khác mlp1 cũng được dò.
                for d in sorted(ud.iterdir()):
                    if d.is_dir() and d.name.lower() not in ("mlp1", "loongmate") and d not in bases:
                        bases.append(d)
        except OSError:
            pass

        # (tên, thư mục emulator dưới .userdata, các thư mục con bên trong, "all" = lấy mọi file, thư mục Saves/ cũ)
        # CHƯA KIỂM CHỨNG: DraStic - máy chưa có save NDS nên chưa biết save thật nằm ở
        # <emu>/backup (.dsv) và <emu>/savestates (.dss) hay chỗ khác; đây là giả định theo code cũ.
        # CHƯA KIỂM CHỨNG: PPSSPP - lấy mọi file dưới PSP/SAVEDATA (mỗi game là một thư mục chứa
        # PARAM.SFO, DATA.BIN...), chưa có save PSP thật trên máy để đối chiếu.
        standalone_adapters = [
            ("Flycast", "flycast", [""], False, "Flycast"),
            ("PPSSPP", "ppsspp", ["PSP/SAVEDATA"], True, "PPSSPP"),
            ("PPSSPP", "ppsspp", ["PSP/PPSSPP_STATE"], False, None),
            ("DraStic", "drastic", ["backup", "savestates"], False, "DraStic"),
            ("N64", "n64", [""], False, "N64"),
            ("Mupen64Plus", "mupen64plus", [""], False, None),
            ("YabaSanshiro", "yabasanshiro", [""], False, "YabaSanshiro"),
        ]
        seen = set()

        def add(emu_name: str, p: Path, filtered: bool, all_files: bool = False):
            if not p.is_dir() or p in seen:
                return
            seen.add(p)
            locations.append({
                "id": f"standalone_{emu_name.lower()}" + (f"_{len(seen)}" if len(seen) > 1 else ""),
                "card": "primary",
                "category": emu_name,
                "path": p,
                "adapter": f"standalone_{emu_name.lower()}",
                "emu_filter": filtered and not all_files,
            })

        for emu_name, emu_dir, inners, all_files, saves_dir in standalone_adapters:
            for base in bases:
                for inner in inners:
                    add(emu_name, base / emu_dir / inner if inner else base / emu_dir, True, all_files)
            if saves_dir:
                add(emu_name, self.primary_path / "Saves" / saves_dir, False)

        return locations

    def build_current_manifest(self, previous_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Scans all discoverable save files and calculates metadata.

        When previous_manifest is given, a file whose rel_path/size/mtime are unchanged
        from the previous manifest reuses that recorded sha256 instead of re-hashing
        (incremental backup: hashing unchanged save data on every run wastes battery/time).
        """
        locations = self.discover_save_locations()
        files_map: Dict[str, Any] = {}
        total_size = 0
        prev_files = (previous_manifest or {}).get("files", {})
        # When the previous manifest was built. Anything modified at or after
        # this moment cannot be trusted to the size+mtime cache (see below).
        prev_mtime_cutoff = (previous_manifest or {}).get("timestamp")

        for loc in locations:
            base_dir = loc["path"]
            adapter = loc["adapter"]
            card = loc["card"]

            for item in base_dir.rglob("*"):
                if item.is_file() and not item.name.startswith("."):
                    if not is_safe_relative_path(base_dir, item):
                        continue
                    if loc.get("emu_filter"):
                        rel_parts = item.relative_to(base_dir).parts
                        if any(p.lower() in _EMU_SKIP_DIRS for p in rel_parts[:-1])                                 or not _is_emu_save_file(item.name):
                            continue
                    stat = item.stat()

                    # Key relative to card root; if that is not possible (location outside
                    # primary/secondary root), use a location-prefixed key so two files with
                    # the same basename in different locations never collide (see S10).
                    card_root = self.primary_path if card == "primary" else self.secondary_path
                    file_base_dir = None
                    try:
                        if card_root is None:
                            raise ValueError("no card root for this location")
                        rel_path = str(item.relative_to(card_root)).replace("\\", "/")
                    except ValueError:
                        rel_path = f"@{loc['id']}/{item.relative_to(base_dir).as_posix()}"
                        file_base_dir = str(base_dir)

                    prev_info = prev_files.get(rel_path)
                    # size+mtime may only be trusted for files that were already
                    # quiet when the previous manifest was built. mtime has
                    # 1-second resolution, and save files very often keep a
                    # constant size (.srm is fixed-size for a given cartridge),
                    # so a save written in the SAME SECOND as the last scan is
                    # byte-for-byte different yet looks identical here. Reusing
                    # the old hash there records a hash whose content is never
                    # stored, and the restore hands back the OLD save -- a
                    # backup that silently does not contain your data.
                    # So: only trust the cache for files strictly older than the
                    # previous scan, and re-hash anything inside that window.
                    # Strictly older than the previous scan: any write that
                    # happened after that scan necessarily stamps mtime with a
                    # later second, so such a file cannot have changed unseen.
                    # Files stamped in the scan's own second stay uncertain and
                    # get re-hashed once; from the next backup on they are quiet
                    # and free again.
                    quiet_before = int(prev_mtime_cutoff) if prev_mtime_cutoff else None
                    if (prev_info
                            and prev_info.get("size") == stat.st_size
                            and prev_info.get("mtime") == int(stat.st_mtime)
                            and quiet_before is not None
                            and int(stat.st_mtime) < quiet_before):
                        sha256 = prev_info["sha256"]
                        hashed = False
                    else:
                        sha256 = compute_sha256(item)
                        hashed = True

                    file_info = {
                        "card": card,
                        "adapter": adapter,
                        "rel_path": rel_path,
                        "absolute_path": str(item),
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "sha256": sha256,
                        "hashed": hashed
                    }
                    if file_base_dir is not None:
                        file_info["base_dir"] = file_base_dir

                    files_map[rel_path] = file_info
                    total_size += stat.st_size

        return {
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(time.time()),
            "total_files": len(files_map),
            "total_size_bytes": total_size,
            "files": files_map
        }

    def _object_path(self, sha256: str) -> Path:
        """Content-addressed object store location for a given sha256 (shared across all snapshots)."""
        return self.objects_dir / sha256[:2] / sha256

    def _store_object(self, src: Path, sha256: str, expected_size: Optional[int] = None) -> None:
        """Copies src into the object store under its hash if not already present (dedup).
        FAT32 has no hardlinks, so content sharing across snapshots works only via this store.
        """
        obj_path = self._object_path(sha256)
        if obj_path.exists():
            return
        _write_file_atomic(src, obj_path)
        if expected_size is not None and obj_path.stat().st_size != expected_size:
            obj_path.unlink(missing_ok=True)
            raise IOError(f"File nguồn đã thay đổi trong lúc sao lưu: {src}")

    def _load_manifest_for_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """Reads a snapshot's manifest.json by raw snapshot_id (caller must have already validated it)."""
        mf_path = self.snapshots_dir / snapshot_id / "manifest.json"
        if not mf_path.exists():
            return None
        try:
            return json.loads(mf_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _load_latest_manifest(self) -> Optional[Dict[str, Any]]:
        """Loads the full manifest of the newest committed snapshot, used as the incremental baseline."""
        snaps = self.list_snapshots()
        if not snaps:
            return None
        return self._load_manifest_for_snapshot(snaps[0]["id"])

    def _next_suffix(self, base_id: str) -> int:
        """Hậu tố cho snapshot mới trong cùng giây+nhãn: 1 = không hậu tố (chưa có gì), n>=2 = "_n".
        TĂNG ĐƠN ĐIỆU: lớn hơn mọi hậu tố đang có trên đĩa VÀ hậu tố đã cấp gần nhất trong tiến trình này
        (kể cả khi bản đó đã bị prune). Nếu chỉ lấy "số trống nhỏ nhất" thì sau khi prune xoá bản gốc,
        snapshot mới lại nhận id không hậu tố, bị _snapshot_sort_key coi là CŨ NHẤT và bị prune ngay."""
        pat = re.compile(r"^" + re.escape(base_id) + r"(?:_(\d+))?(?:\.partial)?$")
        top = 0
        try:
            for d in self.snapshots_dir.iterdir():
                m = pat.match(d.name)
                if m:
                    top = max(top, int(m.group(1)) if m.group(1) else 1)
        except OSError:
            pass
        seen = _LAST_SUFFIX.get(str(self.snapshots_dir))
        if seen and seen[0] == base_id:
            top = max(top, seen[1])
        return top + 1 if top else 1

    def create_snapshot(self, destination: Optional[Path] = None, label: str = "manual") -> Dict[str, Any]:
        """Creates an atomic incremental snapshot. Aborts if disk space is below safety threshold.

        `destination` is kept in the signature for caller compatibility but is no longer supported:
        snapshots must always live under snapshots_dir so retention and object garbage-collection
        can find them. Passing it now returns a clear error instead of silently behaving differently.
        """
        if destination is not None:
            return {
                "success": False,
                "error": "UNSUPPORTED",
                "message": (
                    "Tham số 'destination' không còn được hỗ trợ: snapshot luôn được lưu trong thư mục "
                    "snapshots mặc định để cơ chế giữ phiên bản (retention) và dọn dữ liệu thừa hoạt động đúng."
                )
            }

        safe_label = _LABEL_SANITIZE_RE.sub("", label)[:32] or "manual"

        previous_manifest = self._load_latest_manifest()
        manifest = self.build_current_manifest(previous_manifest)

        # Only objects not already in the content-addressed store need to be written (dedup-aware).
        objects_needed: Dict[str, Dict[str, Any]] = {}
        for info in manifest["files"].values():
            sha = info["sha256"]
            if sha in objects_needed:
                continue
            if not self._object_path(sha).exists():
                objects_needed[sha] = info
        bytes_to_write = sum(info["size"] for info in objects_needed.values())

        free_bytes = shutil.disk_usage(self.userdata_path).free
        if free_bytes - bytes_to_write < 200 * 1024 * 1024:
            need_mb = round(bytes_to_write / (1024 * 1024), 1)
            free_mb = round(free_bytes / (1024 * 1024), 1)
            return {
                "success": False,
                "error": "DISK_LOW_SPACE",
                "message": (
                    f"Cần ghi thêm khoảng {need_mb} MB dữ liệu mới nhưng thẻ chỉ còn {free_mb} MB trống "
                    f"(dưới ngưỡng an toàn 200 MB). Hãy xoá bớt snapshot cũ hoặc giải phóng dung lượng "
                    f"trên thẻ rồi thử sao lưu lại."
                )
            }

        # snapshot_id has 1-second resolution, so two snapshots created in the
        # same second with the same label used to collide -- and the commit step
        # below used to rmtree() whatever was already there, destroying a real
        # snapshot (including the pre-restore safety copy taken moments earlier)
        # while still reporting success. Never reuse an id that already exists:
        # pick the next free suffix instead of overwriting someone's backup.
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        base_id = f"snap_{timestamp}_{safe_label}"
        snapshot_id = base_id
        suffix = self._next_suffix(base_id)
        if suffix > 1:
            snapshot_id = f"{base_id}_{suffix}"
        _LAST_SUFFIX[str(self.snapshots_dir)] = (base_id, suffix)
        target_dir = self.snapshots_dir / snapshot_id
        temp_dir = target_dir.parent / f"{snapshot_id}.partial"

        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)

        try:
            for sha, info in objects_needed.items():
                self._store_object(Path(info["absolute_path"]), sha, expected_size=info["size"])

            # Manifest carries the commit marker and must be written last.
            manifest["snapshot_id"] = snapshot_id
            manifest["committed"] = True
            manifest["layout"] = "objects"
            manifest["label"] = safe_label
            manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
            _write_bytes_atomic(temp_dir / "manifest.json", manifest_bytes)

            # Do NOT rmtree target_dir here: the id was chosen above precisely
            # so nothing exists at this path. If something does, another process
            # won the race -- fail loudly rather than delete a real backup.
            if target_dir.exists():
                raise FileExistsError(
                    f"Snapshot '{snapshot_id}' đã tồn tại. Huỷ để không ghi đè bản sao lưu có sẵn. "
                    f"Hãy thử sao lưu lại sau một giây."
                )
            temp_dir.rename(target_dir)

            self._prune_old_snapshots()

            return {
                "success": True,
                "snapshot_id": snapshot_id,
                "files_count": manifest["total_files"],
                "total_bytes": manifest["total_size_bytes"],
                "bytes_written": bytes_to_write,
                "path": str(target_dir),
                "timestamp": manifest["created_at"]
            }

        except Exception as e:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            return {
                "success": False,
                "error": "BACKUP_FAILED",
                "message": f"Sao lưu thất bại: {str(e)}"
            }

    def list_snapshots(self) -> List[Dict[str, Any]]:
        """Lists valid, committed snapshots sorted newest to oldest."""
        snapshots = []
        if not self.snapshots_dir.exists():
            return snapshots

        for s_dir in sorted(self.snapshots_dir.iterdir(), key=_snapshot_sort_key, reverse=True):
            if s_dir.is_dir() and not s_dir.name.endswith(".partial"):
                mf = s_dir / "manifest.json"
                if mf.exists():
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                        if data.get("committed"):
                            sid = data.get("snapshot_id", s_dir.name)
                            snapshots.append({
                                "id": sid,
                                "label": snapshot_label(sid, data),
                                "created_at": data.get("created_at"),
                                "total_files": data.get("total_files", 0),
                                "total_size_bytes": data.get("total_size_bytes", 0),
                                "path": str(s_dir)
                            })
                    except Exception:
                        pass
        return snapshots

    def _protected_snapshot_ids(self) -> set:
        """Snapshot IDs referenced by an active restore journal (in_progress/failed); never pruned,
        otherwise retention could delete the only safety net a crashed restore can roll back to.
        """
        if not self.journal_file.exists():
            return set(self._restoring)
        try:
            data = json.loads(self.journal_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if data.get("status") not in ("in_progress", "failed"):
            return set()
        ids = set()
        for key in ("snapshot_id", "pre_restore_id"):
            sid = data.get(key)
            if sid:
                ids.add(sid)
        return ids | self._restoring

    def _prune_old_snapshots(self):
        """Giữ N snapshot mới nhất của MỖI nhóm: nhóm auto (nhãn bắt đầu "auto") giữ auto_retention,
        nhóm còn lại (thủ công, pre_restore, ...) giữ max_retention. Snapshot đang được journal
        tham chiếu không bao giờ bị xoá."""
        protected = self._protected_snapshot_ids()
        groups = {True: [], False: []}
        for snap in self.list_snapshots():  # đã sắp mới -> cũ
            groups[snap["label"].startswith(AUTO_LABEL_PREFIX)].append(snap)
        for is_auto, snaps in groups.items():
            keep = self.auto_retention if is_auto else self.max_retention
            for old in snaps[keep:]:
                if old["id"] in protected:
                    continue
                old_path = Path(old["path"])
                if old_path.exists():
                    shutil.rmtree(old_path, ignore_errors=True)
        self._gc_objects()

    def _gc_objects(self) -> None:
        """Deletes object-store blobs no longer referenced by any remaining 'objects' layout snapshot."""
        if not self.objects_dir.exists():
            return

        referenced = set()
        if self.snapshots_dir.exists():
            for s_dir in self.snapshots_dir.iterdir():
                if not s_dir.is_dir() or s_dir.name.endswith(".partial"):
                    continue
                mf = s_dir / "manifest.json"
                if not mf.exists():
                    continue
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if data.get("layout") != "objects":
                    continue
                for info in data.get("files", {}).values():
                    sha = info.get("sha256")
                    if sha:
                        referenced.add(sha)

        for shard in self.objects_dir.iterdir():
            if not shard.is_dir():
                continue
            for obj in shard.iterdir():
                if obj.name.endswith(".partial"):
                    continue
                if obj.name not in referenced:
                    try:
                        obj.unlink()
                    except OSError:
                        pass
            try:
                next(shard.iterdir())
            except StopIteration:
                try:
                    shard.rmdir()
                except OSError:
                    pass

    def _resolve_snapshot(self, snapshot_id: str) -> Optional[Path]:
        """Validates a caller-supplied snapshot_id and resolves it to a path inside snapshots_dir.
        Rejects empty IDs, path separators, '..' and drive letters so a snapshot_id coming from an
        HTTP request body or CLI argument can never reference a path outside snapshots_dir.
        """
        if not snapshot_id:
            return None
        if "/" in snapshot_id or "\\" in snapshot_id or ".." in snapshot_id or ":" in snapshot_id:
            return None
        candidate = self.snapshots_dir / snapshot_id
        if not is_safe_relative_path(self.snapshots_dir, candidate):
            return None
        return candidate

    def preview_restore(self, snapshot_id: str) -> Dict[str, Any]:
        """Compares current files with snapshot without making any modifications."""
        snap_path = self._resolve_snapshot(snapshot_id)
        if snap_path is None:
            return {
                "error": "INVALID_SNAPSHOT_ID",
                "message": f"ID snapshot không hợp lệ: '{snapshot_id}'. Vui lòng chọn lại từ danh sách snapshot."
            }

        mf_path = snap_path / "manifest.json"
        if not mf_path.exists():
            return {"error": "SNAPSHOT_NOT_FOUND", "message": f"Không tìm thấy snapshot {snapshot_id}"}

        snap_manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        current_manifest = self.build_current_manifest()

        to_add = []
        to_update = []
        unchanged = []
        conflicts = []

        snap_files = snap_manifest.get("files", {})
        curr_files = current_manifest.get("files", {})

        for rel_path, s_info in snap_files.items():
            if rel_path not in curr_files:
                to_add.append(rel_path)
            else:
                c_info = curr_files[rel_path]
                if c_info["sha256"] == s_info["sha256"]:
                    unchanged.append(rel_path)
                else:
                    # If current file has newer mtime than snapshot
                    if c_info["mtime"] > s_info["mtime"]:
                        conflicts.append({
                            "rel_path": rel_path,
                            "current_mtime": c_info["mtime"],
                            "snapshot_mtime": s_info["mtime"],
                            "current_size": c_info["size"],
                            "snapshot_size": s_info["size"]
                        })
                    else:
                        to_update.append(rel_path)

        return {
            "snapshot_id": snapshot_id,
            "to_add": to_add,
            "to_update": to_update,
            "unchanged": unchanged,
            "conflicts": conflicts,
            "total_changes": len(to_add) + len(to_update) + len(conflicts)
        }

    def _restore_files(self, snap_path: Path, snap_manifest: Dict[str, Any], rel_paths: List[str],
                        conflict_run_ts: str, on_restored=None) -> Dict[str, Any]:
        """Core restore loop shared by restore_snapshot and rollback_from_journal.

        Resolves each file's source from either the legacy snapshot/data/ layout or the shared
        object store, validates the destination path never escapes its card/location root,
        skips writes to a read-only or near-full secondary card, and preserves conflicting
        destination files into vault_dir/conflicts/<run_ts>/... instead of the Saves folder.
        """
        snap_files = snap_manifest.get("files", {})
        snap_data_dir = snap_path / "data"
        use_legacy = snap_data_dir.is_dir()

        restored = []
        missing = []
        rejected_paths = []
        skipped_readonly = []
        preserved_conflicts = []
        conflict_dir_root = self.conflicts_dir / conflict_run_ts

        for rel_path in rel_paths:
            if rel_path not in snap_files:
                continue
            s_info = snap_files[rel_path]

            if use_legacy:
                src_file = snap_data_dir / rel_path
            else:
                src_file = self._object_path(s_info["sha256"])
            if not src_file.exists():
                missing.append(rel_path)
                continue

            card = s_info.get("card", "primary")

            if rel_path.startswith("@"):
                base_dir_str = s_info.get("base_dir")
                if not base_dir_str or "/" not in rel_path:
                    rejected_paths.append(rel_path)
                    continue
                base_dir = Path(base_dir_str)
                sub_path = rel_path.split("/", 1)[1]
                dest_file = base_dir / sub_path
                check_root = base_dir
            else:
                card_root = self.primary_path if card == "primary" else self.secondary_path
                if not card_root:
                    continue
                dest_file = card_root / rel_path
                check_root = card_root

            if not is_safe_relative_path(check_root, dest_file):
                rejected_paths.append(rel_path)
                continue

            if card == "secondary":
                try:
                    sec_root = self.secondary_path
                    if sec_root is None or not os.access(sec_root, os.W_OK):
                        skipped_readonly.append(rel_path)
                        continue
                    # CHƯA KIỂM CHỨNG TRÊN MÁY THẬT: chưa đo os.access/disk_usage khi SD2 bị tháo giữa chừng
                    # hoặc mount lại read-only trên firmware Leaf thật.
                    if shutil.disk_usage(sec_root).free < 200 * 1024 * 1024:
                        skipped_readonly.append(rel_path)
                        continue
                except OSError:
                    skipped_readonly.append(rel_path)
                    continue

            dest_file.parent.mkdir(parents=True, exist_ok=True)

            if dest_file.exists():
                try:
                    curr_mtime = int(dest_file.stat().st_mtime)
                    is_conflict = curr_mtime > s_info["mtime"] and compute_sha256(dest_file) != s_info["sha256"]
                except OSError:
                    is_conflict = False
                if is_conflict:
                    conflict_name = f"{dest_file.stem}_conflict_{int(time.time())}{dest_file.suffix}"
                    conflict_sub_dir = conflict_dir_root / Path(rel_path).parent
                    conflict_sub_dir.mkdir(parents=True, exist_ok=True)
                    _write_file_atomic(dest_file, conflict_sub_dir / conflict_name)
                    preserved_conflicts.append(conflict_name)

            _write_file_atomic(src_file, dest_file, tmp_suffix=".restore.tmp")
            restored.append(rel_path)
            if on_restored:
                on_restored(rel_path)

        return {
            "restored": restored,
            "missing": missing,
            "rejected_paths": rejected_paths,
            "skipped_readonly": skipped_readonly,
            "preserved_conflicts": preserved_conflicts,
            "conflicts_dir": str(conflict_dir_root) if preserved_conflicts else None
        }

    def restore_snapshot(self, snapshot_id: str, selected_files: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Safely restores files from a snapshot.
        1. Automatically takes a pre-restore backup.
        2. Writes restore journal (updated after every file, so a crash mid-restore is resumable).
        3. Restores with temporary rename.
        4. Preserves conflicting files with a timestamp suffix instead of silent overwriting.
        """
        busy = _busy_refusal("khôi phục save")
        if busy:
            return busy
        snap_path = self._resolve_snapshot(snapshot_id)
        if snap_path is None:
            return {
                "success": False,
                "message": f"ID snapshot không hợp lệ: '{snapshot_id}'. Vui lòng chọn lại từ danh sách snapshot."
            }

        mf_path = snap_path / "manifest.json"
        if not mf_path.exists():
            return {"success": False, "message": "Snapshot không tồn tại"}

        snap_manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        snap_files = snap_manifest.get("files", {})
        target_keys = selected_files if selected_files is not None else list(snap_files.keys())

        # Step 1: Pre-restore snapshot. Fixed label (S5): embedding the source snapshot_id in the
        # label made IDs grow unbounded across repeated restores; the link is recorded in the
        # manifest instead, via a best-effort post-commit annotation (create_snapshot's signature
        # is kept unchanged).
        self._restoring.add(snap_path.name)
        try:
            return self._restore_snapshot_protected(snapshot_id, snap_path, snap_manifest, target_keys)
        finally:
            self._restoring.discard(snap_path.name)

    def _restore_snapshot_protected(self, snapshot_id, snap_path, snap_manifest, target_keys) -> Dict[str, Any]:
        pre_restore = self.create_snapshot(label="pre_restore")
        if not pre_restore.get("success"):
            return {
                "success": False,
                "message": f"Không thể tạo bản sao lưu bảo vệ trước khi khôi phục: {pre_restore.get('message')}"
            }

        pre_mf_path = Path(pre_restore["path"]) / "manifest.json"
        try:
            pre_mf = json.loads(pre_mf_path.read_text(encoding="utf-8"))
            pre_mf["restore_source_snapshot"] = snapshot_id
            _write_bytes_atomic(pre_mf_path, json.dumps(pre_mf, indent=2, ensure_ascii=False).encode("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass  # best-effort annotation only; it must never block the restore itself

        # Step 2: Write Journal (planned_files lets a later crash-recovery pass know how far it got)
        journal_data = {
            "status": "in_progress",
            "snapshot_id": snapshot_id,
            "pre_restore_id": pre_restore["snapshot_id"],
            "started_at": int(time.time()),
            "planned_files": list(target_keys),
            "restored_files": []
        }
        _write_bytes_atomic(self.journal_file, json.dumps(journal_data, indent=2, ensure_ascii=False).encode("utf-8"))

        def on_restored(rel_path: str) -> None:
            journal_data["restored_files"].append(rel_path)
            _write_bytes_atomic(self.journal_file, json.dumps(journal_data, indent=2, ensure_ascii=False).encode("utf-8"))

        run_ts = time.strftime("%Y%m%d_%H%M%S")

        try:
            result = self._restore_files(snap_path, snap_manifest, target_keys, run_ts, on_restored=on_restored)

            journal_data["status"] = "completed"
            journal_data["completed_at"] = int(time.time())
            _write_bytes_atomic(self.journal_file, json.dumps(journal_data, indent=2, ensure_ascii=False).encode("utf-8"))

            out = {
                "success": not result["missing"],
                "snapshot_id": snapshot_id,
                "restored_files_count": len(result["restored"]),
                "missing_files": result["missing"],
                "preserved_conflicts": result["preserved_conflicts"],
                "conflicts_dir": result["conflicts_dir"],
                "rejected_paths": result["rejected_paths"],
                "skipped_readonly": result["skipped_readonly"],
                "pre_restore_id": pre_restore["snapshot_id"]
            }
            if result["missing"]:
                out["message"] = (f"Thiếu dữ liệu của {len(result['missing'])} file trong kho (object bị mất) — các file đó "
                                  f"KHÔNG được khôi phục. Bản an toàn trước khi khôi phục: {pre_restore['snapshot_id']}")
            return out

        except Exception as e:
            journal_data["status"] = "failed"
            journal_data["error"] = str(e)
            _write_bytes_atomic(self.journal_file, json.dumps(journal_data, indent=2, ensure_ascii=False).encode("utf-8"))
            return {
                "success": False,
                "message": f"Khôi phục gặp lỗi: {str(e)}. Bản an toàn được lưu tại {pre_restore['snapshot_id']}"
            }

    def rollback_from_journal(self) -> Dict[str, Any]:
        """Rolls back the files a crashed restore had already written, using the pre-restore snapshot.
        Restores only the files listed in journal['restored_files'] (what actually got overwritten),
        not the full planned set. Does not create a new pre-restore snapshot or journal entry: this
        is the recovery path itself, not a fresh user-initiated restore.
        """
        if not self.journal_file.exists():
            return {"success": False, "message": "Không có nhật ký khôi phục nào để rollback."}
        busy = _busy_refusal("rollback save")
        if busy:
            return busy

        try:
            data = json.loads(self.journal_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"success": False, "message": "Nhật ký khôi phục bị hỏng, không thể rollback."}

        if data.get("status") != "in_progress":
            return {
                "success": False,
                "message": f"Nhật ký không ở trạng thái 'in_progress' (hiện tại: '{data.get('status')}'), không cần rollback."
            }

        pre_restore_id = data.get("pre_restore_id")
        pre_path = self._resolve_snapshot(pre_restore_id) if pre_restore_id else None
        pre_mf_path = (pre_path / "manifest.json") if pre_path else None
        if not pre_path or not pre_mf_path.exists():
            return {
                "success": False,
                "message": (
                    f"Không tìm thấy bản pre-restore snapshot '{pre_restore_id}' để rollback. "
                    f"Hãy kiểm tra dữ liệu thủ công trước khi tiếp tục, đừng tự đoán trạng thái file."
                )
            }

        pre_manifest = json.loads(pre_mf_path.read_text(encoding="utf-8"))
        restored_files = data.get("restored_files", [])

        run_ts = time.strftime("%Y%m%d_%H%M%S")
        result = self._restore_files(pre_path, pre_manifest, restored_files, run_ts)

        data["status"] = "rolled_back"
        data["rolled_back_at"] = int(time.time())
        _write_bytes_atomic(self.journal_file, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))

        return {
            "success": not result["missing"],
            "missing_files": result["missing"],
            "message": f"Đã rollback {len(result['restored'])} file về trạng thái trước khi restore."
                       + (f" THIẾU dữ liệu {len(result['missing'])} file trong kho — các file đó chưa được trả lại."
                          if result["missing"] else ""),
            "restored_files_count": len(result["restored"]),
            "rejected_paths": result["rejected_paths"],
            "skipped_readonly": result["skipped_readonly"],
            "pre_restore_id": pre_restore_id
        }

    def verify_snapshot(self, snapshot_id: str) -> Dict[str, Any]:
        """Re-hashes every file recorded in a snapshot's manifest and checks it against the manifest.
        Detects silent corruption of stored snapshot data (bit rot, truncated writes, tampering).
        """
        snap_path = self._resolve_snapshot(snapshot_id)
        if snap_path is None:
            return {
                "snapshot_id": snapshot_id,
                "ok": False,
                "error": "INVALID_SNAPSHOT_ID",
                "message": f"ID snapshot không hợp lệ: '{snapshot_id}'. Vui lòng chọn lại từ danh sách snapshot."
            }

        mf_path = snap_path / "manifest.json"
        if not mf_path.exists():
            return {
                "snapshot_id": snapshot_id,
                "ok": False,
                "error": "SNAPSHOT_NOT_FOUND",
                "message": f"Không tìm thấy snapshot {snapshot_id}"
            }

        manifest = json.loads(mf_path.read_text(encoding="utf-8"))
        snap_files = manifest.get("files", {})
        snap_data_dir = snap_path / "data"
        use_legacy = snap_data_dir.is_dir()

        missing = []
        size_mismatch = []
        hash_mismatch = []
        checked = 0

        for rel_path, info in snap_files.items():
            src = (snap_data_dir / rel_path) if use_legacy else self._object_path(info["sha256"])
            if not src.exists():
                missing.append(rel_path)
                continue
            checked += 1
            try:
                actual_size = src.stat().st_size
            except OSError:
                missing.append(rel_path)
                continue
            if actual_size != info["size"]:
                size_mismatch.append(rel_path)
                continue
            if compute_sha256(src) != info["sha256"]:
                hash_mismatch.append(rel_path)

        ok = not missing and not size_mismatch and not hash_mismatch
        return {
            "snapshot_id": snapshot_id,
            "ok": ok,
            "checked": checked,
            "missing": missing,
            "size_mismatch": size_mismatch,
            "hash_mismatch": hash_mismatch
        }

    def check_crash_recovery(self) -> Optional[Dict[str, Any]]:
        """Checks if a previous restore was interrupted unexpectedly."""
        if self.journal_file.exists():
            try:
                data = json.loads(self.journal_file.read_text(encoding="utf-8"))
                if data.get("status") == "in_progress":
                    pre_restore_id = data.get("pre_restore_id")
                    can_rollback = bool(pre_restore_id) and (
                        self.snapshots_dir / pre_restore_id / "manifest.json"
                    ).exists()
                    return {
                        "needs_recovery": True,
                        "journal": data,
                        "planned_count": len(data.get("planned_files", [])),
                        "restored_count": len(data.get("restored_files", [])),
                        "can_rollback": can_rollback,
                        "message": "Phát hiện phiên khôi phục bị gián đoạn (mất điện). Có thể dùng pre_restore snapshot để rollback an toàn."
                    }
            except Exception:
                pass
        return None

    # ---------------------------------------------------------------- khôi phục từng game
    def game_index(self, current_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Gom file save/state HIỆN TẠI và trong MỌI snapshot theo game (xem game_of). CHỈ ĐỌC.
        Trả {"games": {key: {"key","name","core","cards","current":[info],"snapshots":{sid:{rel: info}}}},
             "snapshots": [list_snapshots()], "current": manifest}.
        current_manifest: bỏ trống thì tự dựng (tái dùng hash của snapshot mới nhất cho file chưa đổi)."""
        snaps = self.list_snapshots()
        if current_manifest is None:
            current_manifest = self.build_current_manifest(
                self._load_manifest_for_snapshot(snaps[0]["id"]) if snaps else None)
        games: Dict[str, Dict[str, Any]] = {}

        def slot(rel: str):
            key, name, core = game_of(rel)
            g = games.get(key)
            if g is None:
                g = games[key] = {"key": key, "name": name, "core": core, "cards": set(),
                                  "current": [], "snapshots": {}}
            return g

        for rel in sorted(current_manifest.get("files", {})):
            info = current_manifest["files"][rel]
            g = slot(rel)
            g["current"].append(info)
            g["cards"].add(info.get("card", "primary"))
        # snapshot cũ -> mới: game chỉ còn trong snapshot lấy tên của bản mới nhất chứa nó
        for snap in reversed(snaps):
            mf = self._load_manifest_for_snapshot(snap["id"])
            if not mf:
                continue
            for rel, info in mf.get("files", {}).items():
                g = slot(rel)
                g["snapshots"].setdefault(snap["id"], {})[rel] = info
                if not g["current"]:
                    g["name"], g["core"] = game_of(rel)[1], game_of(rel)[2]
                    g["cards"].add(info.get("card", "primary"))
        return {"games": games, "snapshots": snaps, "current": current_manifest}

    def game_dest_path(self, rel_path: str, info: Dict[str, Any]) -> Optional[Path]:
        """Đường đích mà _restore_files sẽ ghi cho rel_path (None nếu không xác định được)."""
        if rel_path.startswith("@"):
            base = info.get("base_dir")
            return Path(base) / rel_path.split("/", 1)[1] if base and "/" in rel_path else None
        root = self.primary_path if info.get("card", "primary") == "primary" else self.secondary_path
        return root / rel_path if root else None

    def verify_restored(self, snapshot_id: str, rel_paths: List[str]) -> List[str]:
        """Băm lại các file vừa khôi phục và đối chiếu sha256 trong manifest của snapshot; trả về danh sách
        rel_path KHÔNG khớp (rỗng = khớp hết). restore_snapshot tự nó không đọc lại để kiểm."""
        snap_path = self._resolve_snapshot(snapshot_id)
        mf = self._load_manifest_for_snapshot(snap_path.name) if snap_path else None
        bad = []
        for rel in rel_paths:
            info = (mf or {}).get("files", {}).get(rel)
            dest = self.game_dest_path(rel, info) if info else None
            try:
                if dest is None or compute_sha256(dest) != info["sha256"]:
                    bad.append(rel)
            except OSError:
                bad.append(rel)
        return bad

    @staticmethod
    def plan_game_restore(game: Dict[str, Any], snapshot_id: str, current_manifest: Dict[str, Any]) -> tuple:
        """Kế hoạch khôi phục file của MỘT game (game = phần tử của game_index()["games"]) từ snapshot_id.
        Trả (plan, to_write): plan = [{"rel","action": create|overwrite|unchanged,"size","current_size",
        "newer_than_snapshot"}] theo rel; to_write = rel cần ghi (không gồm file đã giống hệt). None nếu game
        không có file nào trong snapshot đó. CHỈ ĐỌC."""
        infos = game["snapshots"].get(snapshot_id)
        if infos is None:
            return None
        cur_files = current_manifest.get("files", {})
        plan, to_write = [], []
        for rel in sorted(infos):
            cur, snap = cur_files.get(rel), infos[rel]
            action = "create" if cur is None else "unchanged" if cur["sha256"] == snap["sha256"] else "overwrite"
            plan.append({"rel": rel, "action": action, "size": snap["size"],
                         "current_size": None if cur is None else cur["size"],
                         "newer_than_snapshot": bool(cur is not None and action == "overwrite" and cur["mtime"] > snap["mtime"])})
            if action != "unchanged":
                to_write.append(rel)
        return plan, to_write

    def restore_game_files(self, snapshot_id: str, rel_paths: List[str]) -> Dict[str, Any]:
        """Khôi phục CHỈ rel_paths từ snapshot_id bằng đúng restore_snapshot(selected_files=...) (pre-restore
        snapshot trước khi ghi, journal, bảo toàn xung đột), rồi băm lại từng file đích đối chiếu manifest
        (restore_snapshot tự nó không đọc lại để kiểm). Trả dict restore_snapshot + {"restored","failed","verified"}.

        (restore_snapshot tự bảo vệ snapshot nguồn khỏi bị prune khi tạo pre-restore.)"""
        res = self.restore_snapshot(snapshot_id, selected_files=list(rel_paths))
        if not res.get("success"):
            return res
        bad = self.verify_restored(snapshot_id, list(rel_paths))
        res.update(restored=[r for r in rel_paths if r not in bad], failed=bad, verified=not bad)
        return res
