"""
InventoryEngine module for LoongMate.
Scans and catalogues ROMs, images, saves, states, and BIOS files across Primary and Secondary cards.
Strictly read-only; canonicalizes paths to prevent escaping root directories.
"""

import os
import hashlib
from pathlib import Path, PurePath
from typing import Dict, List, Any, Optional

# Bản sao lưu / file tạm do LoongMate tạo (batchfix .bak-loongmate, ghi nguyên tử .partial/.lmtmp, tải dở .part)
_WORK_SUFFIXES = (".bak-loongmate", ".partial", ".lmtmp", ".part")

def compute_sha256(filepath: Path, chunk_size: int = 65536, max_bytes: Optional[int] = None) -> str:
    """Computes SHA-256 hash of a file safely."""
    h = hashlib.sha256()
    bytes_read = 0
    with open(filepath, "rb") as f:
        while True:
            read_len = chunk_size if max_bytes is None else min(chunk_size, max_bytes - bytes_read)
            if read_len <= 0:
                break
            chunk = f.read(read_len)
            if not chunk:
                break
            h.update(chunk)
            bytes_read += len(chunk)
    return h.hexdigest()

def find_systems_catalog() -> Path:
    """Locates systems.json in both layouts it ships in.

    In the source tree the catalog sits beside engine/ at the project root. In
    a built .pak it is published under res/ (pak/build.sh copies it there), so
    a single hard-coded parent.parent path finds nothing once installed on the
    device and the whole extension/BIOS audit reports itself unavailable.
    Returns the first existing candidate, else the source-tree path so the
    caller still reports a meaningful missing-file location.
    """
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "systems.json",        # source tree / dev checkout
        here.parent / "res" / "systems.json",  # inside a built LoongMate.pak
        here / "systems.json",               # engine/ sibling, belt and braces
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]

def is_safe_relative_path(base: Path, target: Path) -> bool:
    """Ensures target path resolves inside base path without escaping via symlink or .."""
    try:
        resolved_base = base.resolve()
        resolved_target = target.resolve()
        # Component-wise containment check (not a bare string prefix check): a
        # string startswith() would wrongly accept "Roms_evil" as being inside
        # "Roms" because "Roms_evil" also starts with the characters "Roms".
        return resolved_target.is_relative_to(resolved_base)
    except (OSError, RuntimeError):
        return False

class InventoryEngine:
    def __init__(self, primary_path: Path, secondary_path: Optional[Path] = None):
        self.primary_path = primary_path.resolve()
        self.secondary_path = secondary_path.resolve() if secondary_path else None

    # ------------------------------------------------------------------
    # Duyệt cây bằng os.scandir thay cho Path.rglob + resolve() từng file.
    # Đo trên Pocket 1 (FAT32, ~3800 ROM + ~3700 ảnh, 2 thẻ): scan_all 9 s, nhưng
    # scandir+stat thuần cho cùng cây chỉ 0,3 s -> 97% thời gian là chi phí pathlib
    # (dựng Path, relative_to, resolve() cho kiểm tra path traversal). Kết quả (nội dung
    # từng entry, thứ tự: từng thư mục theo tiền thứ tự, trong đó entry theo thứ tự scandir)
    # giữ nguyên như bản rglob.
    #
    # Kiểm tra path traversal: file thường (không phải symlink/junction) nằm trong thư mục
    # thật mà ta tự đi xuống thì chắc chắn nằm trong thư mục gốc -> bỏ resolve(). Chỉ
    # symlink/junction, và mọi thứ bên dưới một thư mục hệ thống là symlink, mới đi đường
    # chậm cũ (is_safe_relative_path).
    # ------------------------------------------------------------------
    @staticmethod
    def _stem_suffix(name: str):
        i = name.rfind(".")
        if 0 < i < len(name) - 1:
            return name[:i], name[i:]
        p = PurePath(name)   # tên lạ ("a.", "...") -> giữ đúng ngữ nghĩa pathlib của bản Python đang chạy
        return p.stem, p.suffix

    def _walk_files(
        self,
        section_dir: Path,
        top: str,
        slow: bool,
        scan_errors: List[Dict[str, Any]],
        unsafe_paths: List[Dict[str, Any]],
    ):
        """Trả list (path, name, rel, stat) cho mọi file hợp lệ dưới `top`, ghi lỗi vào scan_errors.
        Mỗi OSError chỉ làm mất phần liên quan (một entry, một thư mục con)."""
        # Duyệt đệ quy bằng hàm lồng để giữ đúng thứ tự tiền thứ tự của rglob.
        def walk(dirpath: str, rel_prefix: str, is_slow: bool, is_top: bool):
            try:
                with os.scandir(dirpath) as it:
                    entries = list(it)
            except PermissionError:
                if is_top:
                    scan_errors.append({"path": dirpath, "reason": "Permission denied"})
                return   # rglob bỏ qua thư mục con không đọc được
            except OSError as e:
                scan_errors.append({"path": dirpath, "reason": str(e)})
                return
            subdirs = []
            for en in entries:
                name = en.name
                try:
                    is_link = en.is_symlink()
                    is_junction = (not is_link) and getattr(en, "is_junction", lambda: False)()
                    if not is_link and en.is_dir(follow_symlinks=False):
                        subdirs.append((en.path, rel_prefix + name + "/", is_slow or is_junction))
                        continue
                    if not is_link and not en.is_file(follow_symlinks=False):
                        continue
                    if name.startswith(".") or name.lower().endswith(_WORK_SUFFIXES):
                        continue        # file ẩn, bản sao lưu/tạm của LoongMate: không phải game/save/ảnh
                    if is_link or is_slow:
                        item = Path(en.path)
                        if not item.is_file():
                            continue
                        if not is_safe_relative_path(section_dir, item):
                            unsafe_paths.append({
                                "path": str(item),
                                "reason": "Nằm ngoài thư mục gốc cho phép (path traversal/symlink)"
                            })
                            continue
                        st = item.stat()
                    else:
                        st = en.stat(follow_symlinks=False)
                    rel = (rel_prefix + name).replace("\\", "/")
                    yield_list.append((en.path, name, rel, st))
                except OSError as e:
                    scan_errors.append({"path": en.path, "reason": str(e)})
            for sub_path, sub_rel, sub_slow in subdirs:
                walk(sub_path, sub_rel, sub_slow, False)

        yield_list: List[Any] = []
        walk(top, "", slow, True)
        return yield_list

    def _scan_system_folder(
        self,
        section_dir: Path,
        card_id: str,
        include_mtime: bool,
        scan_errors: List[Dict[str, Any]],
        unsafe_paths: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Scans a Roms/ or Images/ style directory organized into per-system subfolders.
        Catches OSError per-item and per-subfolder so one bad entry (or a card pulled
        mid-scan) stops only that part of the scan, not the whole call.
        """
        items: List[Dict[str, Any]] = []
        try:
            sys_dirs = list(section_dir.iterdir())
        except OSError as e:
            scan_errors.append({"path": str(section_dir), "reason": str(e)})
            return items

        for sys_dir in sys_dirs:
            try:
                if not sys_dir.is_dir() or sys_dir.name.startswith("."):
                    continue
                system_name = sys_dir.name
                is_link = sys_dir.is_symlink()
            except OSError as e:
                scan_errors.append({"path": str(sys_dir), "reason": str(e)})
                continue

            for path, name, rel, st in self._walk_files(
                    section_dir, str(sys_dir), is_link, scan_errors, unsafe_paths):
                # relative_path tính từ section_dir: thêm tên thư mục hệ thống ở đầu
                stem, ext = self._stem_suffix(name)
                entry = {
                    "system": system_name,
                    "name": name,
                    "stem": stem,
                    "extension": ext.lower(),
                    "relative_path": (system_name + "/" + rel).replace("\\", "/"),
                    "absolute_path": path,
                    "size": st.st_size,
                    "card_id": card_id
                }
                if include_mtime:
                    entry["mtime"] = int(st.st_mtime)
                items.append(entry)

        return items

    def _scan_flat_folder(
        self,
        section_dir: Path,
        card_id: str,
        include_stem_ext: bool,
        scan_errors: List[Dict[str, Any]],
        unsafe_paths: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Scans a Saves/, States/, or BIOS/ style flat directory (no per-system
        subfolders). Same OSError isolation as _scan_system_folder.
        """
        items: List[Dict[str, Any]] = []
        for path, name, rel, st in self._walk_files(
                section_dir, str(section_dir), False, scan_errors, unsafe_paths):
            entry = {
                "name": name,
                "relative_path": rel,
                "absolute_path": path,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "card_id": card_id
            }
            if include_stem_ext:
                stem, ext = self._stem_suffix(name)
                entry["stem"] = stem
                entry["extension"] = ext.lower()
            items.append(entry)
        return items

    def scan_card(self, root: Path, card_id: str = "primary") -> Dict[str, Any]:
        """Scans a single SD card root directory according to Leaf conventions."""
        result = {
            "card_id": card_id,
            "root": str(root),
            "roms": [],
            "images": [],
            "saves": [],
            "states": [],
            "bios": [],
            "folders_found": [],
            "unsafe_paths": [],
            "scan_errors": []
        }

        if not root.exists() or not root.is_dir():
            return result

        # Check standard folders
        std_folders = ["Roms", "Images", "Saves", "States", "BIOS"]
        for f in std_folders:
            p = root / f
            if p.exists() and p.is_dir():
                result["folders_found"].append(f)

        # 1. Scan Roms
        roms_dir = root / "Roms"
        if roms_dir.exists() and roms_dir.is_dir():
            result["roms"] = self._scan_system_folder(
                roms_dir, card_id, include_mtime=True,
                scan_errors=result["scan_errors"], unsafe_paths=result["unsafe_paths"]
            )

        # 2. Scan Images
        img_dir = root / "Images"
        if img_dir.exists() and img_dir.is_dir():
            result["images"] = self._scan_system_folder(
                img_dir, card_id, include_mtime=False,
                scan_errors=result["scan_errors"], unsafe_paths=result["unsafe_paths"]
            )

        # 3. Scan Saves & States
        saves_dir = root / "Saves"
        if saves_dir.exists() and saves_dir.is_dir():
            result["saves"] = self._scan_flat_folder(
                saves_dir, card_id, include_stem_ext=True,
                scan_errors=result["scan_errors"], unsafe_paths=result["unsafe_paths"]
            )

        states_dir = root / "States"
        if states_dir.exists() and states_dir.is_dir():
            result["states"] = self._scan_flat_folder(
                states_dir, card_id, include_stem_ext=True,
                scan_errors=result["scan_errors"], unsafe_paths=result["unsafe_paths"]
            )

        # 4. Scan BIOS
        bios_dir = root / "BIOS"
        if bios_dir.exists() and bios_dir.is_dir():
            result["bios"] = self._scan_flat_folder(
                bios_dir, card_id, include_stem_ext=False,
                scan_errors=result["scan_errors"], unsafe_paths=result["unsafe_paths"]
            )

        return result

    def scan_all(self) -> Dict[str, Any]:
        """Scans Primary and Secondary cards and yields a unified catalog.

        Luôn quét thật (không cache): ~0,5 s cho ~7.500 file trên Pocket 1. Kết quả chỉ để HIỂN THỊ;
        mọi thao tác xoá/chuyển vẫn tự kiểm file thật (execute_deletion băm mới, move kiểm size/mtime).
        """
        primary_scan = self.scan_card(self.primary_path, "primary")
        secondary_scan = self.scan_card(self.secondary_path, "secondary") if self.secondary_path else None

        unsafe_paths = list(primary_scan["unsafe_paths"])
        scan_errors = list(primary_scan["scan_errors"])
        if secondary_scan:
            unsafe_paths.extend(secondary_scan["unsafe_paths"])
            scan_errors.extend(secondary_scan["scan_errors"])

        # Merge games, images, saves, states, bios
        unified = {
            "primary": primary_scan,
            "secondary": secondary_scan,
            "total_roms": len(primary_scan["roms"]) + (len(secondary_scan["roms"]) if secondary_scan else 0),
            "total_saves": len(primary_scan["saves"]) + (len(secondary_scan["saves"]) if secondary_scan else 0),
            "total_states": len(primary_scan["states"]) + (len(secondary_scan["states"]) if secondary_scan else 0),
            "total_images": len(primary_scan["images"]) + (len(secondary_scan["images"]) if secondary_scan else 0),
            "total_bios": len(primary_scan["bios"]) + (len(secondary_scan["bios"]) if secondary_scan else 0),
            "unsafe_paths": unsafe_paths,
            "scan_errors": scan_errors
        }
        return unified
