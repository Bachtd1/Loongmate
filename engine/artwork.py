"""
Ảnh bìa chéo thẻ cho LoongMate (nâng cấp #8) + vài hàm chép an toàn dùng chung với transfer.py.

API công khai (dành cho người ghép CLI/UI):

  find_art(rom_path, primary_path, secondary_path) -> Optional[Path]
      Tìm ảnh bìa của ROM trên CẢ HAI thẻ: Images/<HỆ>/<stem ROM>.<png|jpg|...>,
      cùng hệ máy kể cả tên cũ/alias trong systems.json (Images/MD cho Roms/MEGADRIVE...).
      Ưu tiên ảnh nằm trên thẻ chứa ROM, rồi thẻ còn lại. Không đệ quy thư mục con.

  ensure_art_on_card(rom_path, card_root, primary_path, secondary_path, app_data) -> dict
      Nếu thẻ `card_root` chưa có ảnh cho ROM mà thẻ kia có, chép ảnh về
      card_root/Images/<thư mục hệ của ROM>/<stem ROM><đuôi ảnh gốc>.
      Chép có xác minh SHA-256 (ghi .partial rồi os.replace). Thẻ đã có ảnh cho game (dù khác nội dung)
      thì giữ nguyên, KHÔNG ghi đè.
      Trả: {"ok": bool, "status": "copied"|"already_present"|"no_art"|"refused"|"error",
            "src": str|None, "dst": str|None, "message": str}
      "ok" là True với copied / already_present / no_art (không có gì để chép không phải lỗi).

  Dùng cho dedupe (execute_deletion(copy_artwork=True)) và cho transfer.

Hàm dùng chung (nội bộ, transfer.py nhập lại): ledger_append, copy_file_hashed, canon_system,
system_dirs, find_art_in_card, locate_rom.

# CHƯA KIỂM CHỨNG: Leaf có tự tìm ảnh bìa chéo thẻ không. Tính năng này là bảo hiểm:
# chép thừa một ảnh nhỏ còn hơn game mất ảnh bìa.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from engine.inventory import compute_sha256, find_systems_catalog, is_safe_relative_path

LEDGER_NAME = "transfer-ledger.jsonl"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
_CHUNK = 1024 * 1024

_catalog_cache: Dict[str, Dict[str, str]] = {}


# --------------------------------------------------------------------------
# helpers dùng chung
# --------------------------------------------------------------------------
def _canon_map() -> Dict[str, str]:
    """UPPER(folder | id | alias) -> UPPER(folder chuẩn), đọc từ systems.json."""
    path = find_systems_catalog()
    key = str(path)
    if key not in _catalog_cache:
        m: Dict[str, str] = {}
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for s in data.get("systems", []):
                folder = (s.get("folder") or s.get("id") or "").upper()
                if not folder:
                    continue
                for name in [s.get("folder"), s.get("id")] + list(s.get("aliases", [])):
                    if name:
                        m.setdefault(name.upper(), folder)
        except (OSError, ValueError):
            m = {}
        _catalog_cache[key] = m
    return _catalog_cache[key]


def canon_system(name: str) -> str:
    """Tên hệ chuẩn (UPPER); hệ lạ thì giữ nguyên tên (UPPER)."""
    return _canon_map().get(name.upper(), name.upper())


def system_dirs(card_root: Path, section: str, system: str) -> List[Path]:
    """Các thư mục card_root/<section>/<X> cùng hệ với `system` (kể cả alias); thư mục trùng tên đứng đầu."""
    base = Path(card_root) / section
    want = canon_system(system)
    try:
        dirs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
                and canon_system(d.name) == want]
    except OSError:
        return []
    return sorted(dirs, key=lambda d: (d.name.lower() != system.lower(), d.name))


def locate_rom(rom_path, primary_path, secondary_path) -> Optional[Tuple[Path, str, Path]]:
    """(thư mục gốc thẻ, tên thư mục hệ, đường dẫn tương đối dưới Roms/) nếu rom_path nằm trong
    Roms/<HỆ>/... của một thẻ; ngược lại None."""
    p = Path(rom_path)
    for root in (primary_path, secondary_path):
        if root and is_safe_relative_path(Path(root) / "Roms", p):
            card = Path(root).resolve()
            try:
                rel = p.resolve().relative_to(card / "Roms")
            except ValueError:
                continue
            if len(rel.parts) >= 2:
                return card, rel.parts[0], rel
    return None


def find_art_in_card(card_root: Path, system: str, stem: str) -> Optional[Path]:
    """Ảnh <stem>.<đuôi ảnh> (không phân biệt hoa thường) trong Images/<hệ tương đương> của một thẻ."""
    stem_l = stem.lower()
    for d in system_dirs(card_root, "Images", system):
        try:
            for f in sorted(d.iterdir(), key=lambda x: x.name):
                if (f.suffix.lower() in IMAGE_EXTS and f.stem.lower() == stem_l
                        and f.is_file() and not f.is_symlink()):
                    return f
        except OSError:
            continue
    return None


def ledger_append(app_data, entry: Dict[str, Any]) -> None:
    """Thêm một dòng JSON vào <app_data>/transfer-ledger.jsonl rồi fsync. Ném OSError nếu không bền được."""
    app_data = Path(app_data)
    app_data.mkdir(parents=True, exist_ok=True)
    entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    with open(app_data / LEDGER_NAME, "ab") as f:
        f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return  # Windows không mở được thư mục; chỉ là nỗ lực tốt nhất
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def copy_file_hashed(src: Path, dst: Path) -> str:
    """Chép src -> dst qua dst.partial (fsync, copystat, os.replace). Trả SHA-256 của dữ liệu ĐỌC TỪ NGUỒN.
    Người gọi phải tự băm lại dst và so sánh. Lỗi thì dọn .partial rồi ném OSError."""
    import hashlib
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    h = hashlib.sha256()
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while True:
                chunk = fi.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                fo.write(chunk)
            fo.flush()
            os.fsync(fo.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dst)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(dst.parent)
    return h.hexdigest()


# --------------------------------------------------------------------------
# API công khai
# --------------------------------------------------------------------------
def find_art(rom_path, primary_path, secondary_path) -> Optional[Path]:
    loc = locate_rom(rom_path, primary_path, secondary_path)
    if loc is None:
        return None
    own, system, _rel = loc
    roots = [own] + [Path(r).resolve() for r in (primary_path, secondary_path)
                     if r and Path(r).resolve() != own]
    stem = Path(rom_path).stem
    for root in roots:
        art = find_art_in_card(root, system, stem)
        if art is not None:
            return art
    return None


def ensure_art_on_card(rom_path, card_root, primary_path, secondary_path, app_data) -> Dict[str, Any]:
    res: Dict[str, Any] = {"ok": False, "status": "error", "src": None, "dst": None, "message": ""}
    card = Path(card_root).resolve()
    allowed = [Path(r).resolve() for r in (primary_path, secondary_path) if r]
    loc = locate_rom(rom_path, primary_path, secondary_path)
    if card not in allowed or loc is None or loc[0] != card:
        res["status"] = "refused"
        res["message"] = "Từ chối: ROM phải nằm trong Roms/ của đúng thẻ card_root (Primary hoặc Secondary)."
        return res
    _own, system, _rel = loc
    stem = Path(rom_path).stem

    existing = find_art_in_card(card, system, stem)
    src = None
    for root in allowed:
        if root != card:
            src = find_art_in_card(root, system, stem)
            if src is not None:
                break
    if src is None:
        res.update(ok=True, status="no_art", message="Không thẻ nào có ảnh bìa cho game này — không có gì để chép.")
        return res

    res["src"] = str(src)
    dst = card / "Images" / system / (stem + src.suffix.lower())
    if existing is not None:
        # Thẻ này đã có ảnh cho game (dù nội dung có khác ảnh ở thẻ kia): không chép, không ghi đè.
        res["dst"] = str(existing)
        res.update(ok=True, status="already_present",
                   message=f"Thẻ này đã có ảnh bìa '{existing.name}' — giữ nguyên, không ghi đè.")
        return res

    res["dst"] = str(dst)
    if dst.is_symlink() or src.is_symlink():
        res.update(status="refused", message="Từ chối: ảnh là symlink.")
        return res
    entry = {"op": "artwork_copy", "src": str(src), "dst": str(dst), "kind": "artwork"}
    try:
        ledger_append(app_data, {**entry, "status": "intent"})
        want = copy_file_hashed(src, dst)
        ledger_append(app_data, {**entry, "status": "copied", "sha256": want})
        if compute_sha256(dst) != want:
            try:
                os.remove(dst)
            except OSError:
                pass
            ledger_append(app_data, {**entry, "status": "failed", "error": "hash lệch"})
            res.update(status="error", message="Ảnh chép sang bị lệch SHA-256 — đã xoá bản chép dở, ảnh gốc còn nguyên.")
            return res
        ledger_append(app_data, {**entry, "status": "verified", "sha256": want})
    except OSError as e:
        res.update(status="error", message=f"Không chép được ảnh bìa: {e}")
        return res
    res.update(ok=True, status="copied", message=f"Đã chép ảnh bìa sang {dst} (đã xác minh SHA-256).")
    return res
