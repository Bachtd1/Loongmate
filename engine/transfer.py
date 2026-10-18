"""
Chuyển game giữa hai thẻ SD (nâng cấp #3) — giải phóng thẻ SD1 mà không phải xoá game.

API công khai (dành cho người ghép CLI/UI):

  plan_move(rom_path, primary_path, secondary_path, dest_card,
            include_saves=True, include_artwork=True) -> dict
      dest_card: "primary" | "secondary". Chỉ ĐỌC, không đổi gì trên thẻ.
      Trả {"ok", "files": [{"src","dst","size","kind","remove_src"}], "bytes", "bytes_freed",
           "warnings", "message", "game", "source_card", "dest_card"}
      kind: rom | track | playlist | artwork | save | state.
      Đơn vị chuyển là CẢ GAME: .cue + track, .m3u + đĩa + .cue của đĩa; ảnh bìa cùng stem;
      save/state cùng stem (nếu include_saves). remove_src=False nghĩa là chỉ CHÉP, giữ bản ở nguồn.
      Từ chối (ok=False) nếu: ROM ngoài Roms/ hai thẻ, symlink, .gdi/.ccd, một file của game đang
      được playlist KHÁC tham chiếu (chuyển sẽ làm hỏng game kia), hoặc thẻ đích sau khi chép còn < 200 MB.

  execute_move(plan, app_data, primary_path, secondary_path) -> dict
      Trả {"moved": [{"src","dst","kind","size"}], "copied": [... chỉ chép, nguồn còn nguyên (save/state...)],
           "refused": [{"path","reason"}], "bytes_moved", "rolled_back": bool}
      bytes_moved = số byte đã được giải phóng khỏi thẻ nguồn.
      Quy trình mỗi file: ghi sổ (intent) -> chép ra .partial -> fsync -> os.replace -> băm SHA-256 bản đích
      so với dữ liệu đọc từ nguồn -> ghi sổ (copied, verified). CHỈ SAU KHI CẢ GAME đã chép và xác minh
      xong mới xoá nguồn (ghi sổ source_remove_intent trước, source_removed sau). Nguyên tử theo game:
      một file lỗi/lệch hash thì xoá hết bản đích đã chép của game đó, nguồn còn nguyên (rolled_back=True).
      Đích đã có file cùng tên: cùng hash thì coi là đã có (vẫn chỉ xoá nguồn sau khi xác minh);
      khác hash thì từ chối cả game, không ghi đè. Sổ: <app_data>/transfer-ledger.jsonl.

  plan_bulk_move(inventory, primary_path, secondary_path, dest_card,
                 target_free_mb=None, system=None) -> dict
      Đề xuất danh sách game nên chuyển TỪ thẻ còn lại SANG dest_card để thẻ nguồn còn trống >= target_free_mb.
      `inventory` là kết quả InventoryEngine.scan_all(). Trả {"ok","moves":[{"rom_path","name","system",
      "bytes_freed","bytes_copy","has_save","plan","discs"}],"skipped","reached","free_before_mb","free_after_mb",
      "bytes_freed","bytes_copy","warnings","message"}. Mỗi phần tử "plan" đưa thẳng cho execute_move.
      Ứng viên là ĐƠN VỊ GAME, không phải từng file: .m3u nếu có (gom các đĩa), không có thì .cue, không
      có thì file đơn. Game nhiều đĩa KHÔNG có .m3u (>= 2 số đĩa "(Disc N)" khác nhau, cùng thư mục) là
      MỘT ứng viên gồm mọi đĩa (rom_path = .cue của đĩa nhỏ số nhất, "discs" = số đĩa, plan gộp các đĩa).
      "name" = tên game: .m3u/.cue -> tên không đuôi (game nhiều đĩa: tên chung), file đơn giữ nguyên tên file.
      Một .cue nằm trong .m3u không bao giờ được đề xuất riêng.

Save/state khi chuyển thẻ:
# CHƯA KIỂM CHỨNG: RetroArch/Leaf tìm save theo thẻ chứa ROM hay luôn ở thẻ Primary. Vì vậy save/state được
# CHÉP sang thẻ đích nhưng KHÔNG xoá ở nguồn (thà thừa một bản còn hơn mất tiến trình chơi).
"""

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.artwork import (IMAGE_EXTS, canon_system, copy_file_hashed, find_art_in_card,
                            ledger_append, locate_rom, system_dirs)
from engine.batchfix import DISC_RE
from engine.dedupe import PLAYLIST_EXTS, _expand, _key, _read_refs
from engine.inventory import compute_sha256, find_systems_catalog, is_safe_relative_path

MB = 1024 * 1024
MIN_FREE_AFTER = 200 * MB          # thẻ đích sau khi chép phải còn >= 200 MB
RECENT_SAVE_SECS = 3600            # save/state sửa trong 1 giờ qua = coi là game đang chơi
SECTION = {"rom": "Roms", "track": "Roms", "playlist": "Roms",
           "artwork": "Images", "save": "Saves", "state": "States"}


def _free_bytes(root: Path) -> int:
    try:
        return shutil.disk_usage(root).free
    except OSError:
        return 0


def _roots(primary_path, secondary_path) -> Dict[str, Optional[Path]]:
    return {"primary": Path(primary_path).resolve() if primary_path else None,
            "secondary": Path(secondary_path).resolve() if secondary_path else None}


def _find_existing(dst: Path) -> Optional[Path]:
    """File đã có ở đích (FAT không phân biệt hoa thường nên so tên theo chữ thường)."""
    if os.path.lexists(dst):
        return dst
    try:
        low = dst.name.lower()
        for f in dst.parent.iterdir():
            if f.name.lower() == low:
                return f
    except OSError:
        pass
    return None


def _is_save_of(path: Path, stems: set) -> bool:
    s = path.stem.lower()
    return s in stems or re.sub(r"\.state.*$", "", s) in stems


# --------------------------------------------------------------------------
# plan_move
# --------------------------------------------------------------------------
def plan_move(rom_path, primary_path, secondary_path, dest_card,
              include_saves: bool = True, include_artwork: bool = True) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "files": [], "bytes": 0, "bytes_freed": 0, "warnings": [],
                           "message": "", "game": None, "source_card": None, "dest_card": dest_card}
    roots = _roots(primary_path, secondary_path)
    if dest_card not in ("primary", "secondary"):
        out["message"] = "Từ chối: dest_card phải là 'primary' hoặc 'secondary'."
        return out
    dest_root = roots[dest_card]
    if dest_root is None or not dest_root.is_dir():
        out["message"] = f"Từ chối: thẻ đích '{dest_card}' không có hoặc không gắn. Kiểm tra thẻ rồi thử lại."
        return out
    p = Path(rom_path)
    loc = locate_rom(p, primary_path, secondary_path)
    if loc is None or not p.is_file() or p.is_symlink():
        out["message"] = ("Từ chối: đường dẫn phải là file thường có thật (không phải symlink) nằm trong "
                          "Roms/<HỆ>/ của thẻ Primary hoặc Secondary. Hãy truyền đường dẫn đầy đủ tới file ROM.")
        return out
    card, sys_name, _rel = loc
    if card == dest_root:
        out["message"] = f"Từ chối: game đã nằm ở thẻ đích '{dest_card}'. Chọn thẻ đích còn lại."
        return out
    out["source_card"] = "primary" if card == roots["primary"] else "secondary"
    rp = p.resolve()
    ext = rp.suffix.lower()
    out["game"] = rp.name
    if ext in (".gdi", ".ccd"):
        out["message"] = (f"Chưa hỗ trợ chuyển game dạng '{ext}' (không biết chắc những file nào thuộc game). "
                          "Hãy chuyển thủ công cả thư mục game.")
        return out

    sysdir = card / "Roms" / sys_name
    index = {_key(f): f for f in sysdir.rglob("*") if f.is_file()}
    files = {_key(rp): rp}
    if ext in PLAYLIST_EXTS:
        found, missing = _expand(rp, index)
        files.update({_key(f): f for f in found})
        for m in missing:
            out["warnings"].append(f"'{rp.name}' tham chiếu '{m}' nhưng file này không có trên thẻ.")

    # File mà playlist KHÔNG nằm trong game này còn tham chiếu thì không được chuyển (sẽ hỏng game kia).
    playlists = {k: f for k, f in index.items() if f.suffix.lower() in PLAYLIST_EXTS}
    held = set()
    for k, f in playlists.items():
        if k not in files:
            held.update(_key(r) for r in _read_refs(f))
    blocked = [files[k].name for k in files if k in held]
    if blocked:
        out["message"] = (f"Từ chối: '{blocked[0]}' còn được game/playlist khác tham chiếu — chuyển riêng sẽ làm hỏng "
                          "game đó. Hãy chọn file .cue/.m3u của game để chuyển trọn bộ.")
        return out
    if any(f.is_symlink() for f in files.values()):
        out["message"] = "Từ chối: game chứa symlink, không chuyển để tránh trỏ ra ngoài thẻ."
        return out

    entries: List[Dict[str, Any]] = []

    def add(src: Path, base: Path, kind: str, remove_src: bool):
        entries.append({"src": str(src), "dst": str(dest_root / base.name / src.relative_to(card / base.name)),
                        "size": src.stat().st_size, "kind": kind, "remove_src": remove_src})

    for k, f in sorted(files.items(), key=lambda kv: str(kv[1])):
        if f == rp:
            kind = "playlist" if ext in PLAYLIST_EXTS else "rom"
        else:
            kind = "playlist" if f.suffix.lower() in PLAYLIST_EXTS else "track"
        add(f, Path("Roms"), kind, True)

    stems = {f.stem.lower() for f in files.values()}
    if include_artwork:
        art = find_art_in_card(card, sys_name, rp.stem)
        if art is not None and find_art_in_card(dest_root, sys_name, rp.stem) is None:
            others = [f for d in system_dirs(card, "Roms", sys_name) for f in d.rglob("*")
                      if f.is_file() and f.stem.lower() == rp.stem.lower() and _key(f) not in files]
            if others:
                out["warnings"].append(f"Ảnh bìa được chép sang nhưng GIỮ lại ở nguồn vì còn bản khác cùng tên "
                                       f"'{others[0].name}' đang dùng nó.")
            add(art, Path("Images"), "artwork", not others)

    if include_saves:
        for sub, kind in (("Saves", "save"), ("States", "state")):
            base = card / sub
            if not base.is_dir():
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file() and not f.is_symlink() and not f.name.startswith(".") and _is_save_of(f, stems):
                    add(f, Path(sub), kind, False)
        if any(e["kind"] in ("save", "state") for e in entries):
            out["warnings"].append(
                "Save/state được CHÉP sang thẻ đích nhưng KHÔNG xoá ở thẻ nguồn. "
                "# CHƯA KIỂM CHỨNG: RetroArch/Leaf tìm save theo thẻ chứa ROM hay luôn ở Primary; "
                "thà thừa một bản còn hơn mất tiến trình chơi.")

    def _already(e) -> bool:
        ex = _find_existing(Path(e["dst"]))
        return ex is not None and ex.is_file() and ex.stat().st_size == e["size"]

    need = sum(e["size"] for e in entries if not _already(e))
    free = _free_bytes(dest_root)
    if free - need < MIN_FREE_AFTER:
        out["message"] = (f"Từ chối: thẻ đích chỉ còn {free / MB:.0f} MB, cần chép {need / MB:.1f} MB và phải còn lại "
                          f"ít nhất {MIN_FREE_AFTER // MB} MB sau khi chép.")
        return out

    out["files"] = entries
    out["bytes"] = sum(e["size"] for e in entries)
    out["bytes_freed"] = sum(e["size"] for e in entries if e["remove_src"])
    out["ok"] = True
    n_rom = sum(1 for e in entries if e["kind"] in ("rom", "track", "playlist"))
    out["message"] = (f"Sẽ chuyển '{rp.name}' từ {out['source_card']} sang {dest_card}: {n_rom} file ROM, "
                      f"{len(entries) - n_rom} file đi kèm, giải phóng {out['bytes_freed'] / MB:.1f} MB ở thẻ nguồn.")
    return out


# --------------------------------------------------------------------------
# execute_move
# --------------------------------------------------------------------------
class _Abort(Exception):
    pass


def _cleanup_dirs(dirs: List[Path]) -> None:
    for d in sorted(set(dirs), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass


def execute_move(plan, app_data, primary_path, secondary_path) -> Dict[str, Any]:
    res: Dict[str, Any] = {"moved": [], "copied": [], "refused": [], "bytes_moved": 0, "rolled_back": False}
    app_data = Path(app_data)
    entries = list((plan or {}).get("files") or [])

    def refuse(why: str):
        res["refused"] = [{"path": e.get("src", "?"), "reason": why} for e in entries] or \
                         [{"path": "?", "reason": why}]
        return res

    if not plan or not plan.get("ok") or not entries:
        return refuse("Kế hoạch không hợp lệ hoặc rỗng (plan['ok'] phải là True). Chạy lại plan_move.")
    roots = _roots(primary_path, secondary_path)
    dest_id = plan.get("dest_card")
    dest_root = roots.get(dest_id) if dest_id in roots else None
    if dest_root is None or not dest_root.is_dir():
        return refuse("Thẻ đích không có hoặc không gắn — không chuyển gì.")

    # ---- 1. kiểm biên toàn bộ game trước khi đụng vào bất cứ thứ gì ----
    items: List[Dict[str, Any]] = []
    seen = set()
    for e in entries:
        kind = e.get("kind")
        sec = SECTION.get(kind)
        src, dst = Path(str(e.get("src"))), Path(str(e.get("dst")))
        if sec is None:
            return refuse(f"Loại file không hợp lệ: '{kind}'.")
        card = next((c for c in roots.values() if c is not None and c != dest_root
                     and is_safe_relative_path(c / sec, src)), None)
        if card is None:
            return refuse(f"'{src}' nằm ngoài {sec}/ của thẻ nguồn — từ chối cả game.")
        if src.is_symlink() or not src.is_file():
            return refuse(f"'{src}' không phải file thường (không tồn tại hoặc là symlink) — từ chối cả game.")
        if kind == "artwork" and src.suffix.lower() not in IMAGE_EXTS:
            return refuse(f"'{src}' không phải ảnh — từ chối cả game.")
        expect = dest_root / sec / src.resolve().relative_to(card / sec)
        if not is_safe_relative_path(dest_root / sec, dst) or dst.resolve() != expect.resolve():
            return refuse(f"Đích '{dst}' không khớp vị trí mong đợi '{expect}' trong {sec}/ của thẻ đích — từ chối cả game.")
        if _key(src) in seen:
            continue
        seen.add(_key(src))
        items.append({"src": src, "dst": expect, "kind": kind, "remove_src": bool(e.get("remove_src")),
                      "size": src.stat().st_size, "st": None, "sha": None, "present": False})

    # ---- 2. đích đã có file cùng tên? (cùng hash = đã có, khác hash = từ chối) ----
    try:
        for it in items:
            ex = _find_existing(it["dst"])
            if ex is None:
                continue
            if ex.is_symlink() or not ex.is_file():
                return refuse(f"Đích '{ex}' đã tồn tại và không phải file thường — từ chối cả game, không ghi đè.")
            if ex.stat().st_size != it["size"]:
                return refuse(f"Đích '{ex}' đã có file cùng tên nhưng khác dung lượng — từ chối cả game, không ghi đè.")
            st = it["src"].stat()
            h_src = compute_sha256(it["src"])
            if compute_sha256(ex) != h_src:
                return refuse(f"Đích '{ex}' đã có file cùng tên nhưng khác nội dung (SHA-256) — từ chối cả game, không ghi đè.")
            it.update(present=True, sha=h_src, st=(st.st_size, st.st_mtime_ns))
    except OSError as e:
        return refuse(f"Lỗi I/O khi kiểm tra đích: {e}")

    need = sum(it["size"] for it in items if not it["present"])
    free = _free_bytes(dest_root)
    if free - need < MIN_FREE_AFTER:
        return refuse(f"Thẻ đích chỉ còn {free / MB:.0f} MB, cần chép {need / MB:.1f} MB và phải còn lại "
                      f"ít nhất {MIN_FREE_AFTER // MB} MB — từ chối, không chép gì.")

    # ---- 3. sổ intent TRƯỚC khi chép ----
    game = plan.get("game")
    try:
        for it in items:
            ledger_append(app_data, {"op": "move", "game": game, "src": str(it["src"]), "dst": str(it["dst"]),
                                     "kind": it["kind"], "size": it["size"], "status": "intent"})
    except OSError as e:
        return refuse(f"Không ghi được sổ chuyển ({e}) — không chuyển khi chưa có dấu vết.")

    # ---- 4. chép + xác minh từng file; lỗi thì hoàn tác cả game ----
    created_files: List[Path] = []
    created_dirs: List[Path] = []
    bad: Optional[str] = None
    bad_path = ""
    try:
        for it in items:
            if it["present"]:
                continue
            bad_path = str(it["src"])
            d = it["dst"].parent
            chain = []
            while not d.exists() and d != dest_root:
                chain.append(d)
                d = d.parent
            created_dirs.extend(chain)
            st0 = it["src"].stat()
            created_files.append(it["dst"])
            want = copy_file_hashed(it["src"], it["dst"])
            ledger_append(app_data, {"op": "move", "game": game, "src": str(it["src"]), "dst": str(it["dst"]),
                                     "kind": it["kind"], "sha256": want, "status": "copied"})
            if compute_sha256(it["dst"]) != want:
                raise _Abort("SHA-256 bản đích lệch so với nguồn")
            st1 = it["src"].stat()
            if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
                raise _Abort("file nguồn bị sửa trong lúc chép")
            it["sha"], it["st"] = want, (st1.st_size, st1.st_mtime_ns)
            ledger_append(app_data, {"op": "move", "game": game, "src": str(it["src"]), "dst": str(it["dst"]),
                                     "kind": it["kind"], "sha256": want, "status": "verified"})
    except (OSError, _Abort) as e:
        bad = str(e)
    if bad is not None:
        for f in created_files:
            for g in (f, f.with_name(f.name + ".partial")):
                try:
                    os.remove(g)
                except OSError:
                    pass
        _cleanup_dirs(created_dirs)
        try:
            ledger_append(app_data, {"op": "move", "game": game, "status": "rolled_back", "reason": bad})
        except OSError:
            pass
        res["rolled_back"] = True
        res["refused"].append({"path": bad_path, "reason": f"Hoàn tác cả game, nguồn còn nguyên: {bad}."})
        return res

    # ---- 5. cả game đã xác minh: giờ mới xoá nguồn (chỉ file remove_src) ----
    for it in items:
        base = {"src": str(it["src"]), "dst": str(it["dst"]), "kind": it["kind"], "size": it["size"]}
        if not it["remove_src"]:
            res["copied"].append(base)
            continue
        try:
            st = it["src"].stat()
            if (st.st_size, st.st_mtime_ns) != it["st"]:
                res["refused"].append({"path": base["src"], "reason": "Nguồn đã đổi sau khi xác minh — giữ nguồn, không xoá."})
                continue
            ledger_append(app_data, {"op": "move", "game": game, **base, "sha256": it["sha"],
                                     "status": "source_remove_intent"})
            os.remove(it["src"])
            ledger_append(app_data, {"op": "move", "game": game, **base, "sha256": it["sha"],
                                     "status": "source_removed"})
        except OSError as e:
            res["refused"].append({"path": base["src"], "reason": f"Bản đích đã đủ nhưng không xoá được nguồn: {e}"})
            continue
        res["moved"].append(base)
        res["bytes_moved"] += it["size"]
        # dọn thư mục con rỗng trong Roms/<HỆ>/ (không bao giờ xoá thư mục hệ)
        par = it["src"].parent
        for _ in range(8):
            if par.parent.name.lower() in ("roms", "images", "saves", "states") or par == par.parent:
                break
            try:
                par.rmdir()
            except OSError:
                break
            par = par.parent
    return res


# --------------------------------------------------------------------------
# kế hoạch đã duyệt: đóng dấu / kiểm dấu
# --------------------------------------------------------------------------
def stamp_plan(plan) -> Dict[str, Any]:
    """Bản sao của plan_move kèm "mtime_ns" của từng file nguồn (CHỈ ĐỌC). `size` đã có sẵn trong mỗi mục.
    Dùng cho kế hoạch lưu ra file: check_plan_stamps so lại lúc thực hiện."""
    files = []
    for e in plan.get("files", []):
        try:
            mt = Path(e["src"]).stat().st_mtime_ns
        except OSError:
            mt = None
        files.append({**e, "mtime_ns": mt})
    return {**plan, "files": files}


def check_plan_stamps(plan) -> Optional[str]:
    """None nếu mọi file nguồn còn đúng size + mtime_ns như lúc đóng dấu; ngược lại câu lý do (tiếng Việt)."""
    for e in plan.get("files", []):
        if e.get("mtime_ns") is None:
            return f"'{e.get('src')}' không có dấu thời gian lúc lập kế hoạch — lập lại kế hoạch."
        try:
            st = Path(e["src"]).stat()
        except OSError:
            return f"'{e.get('src')}' không còn (hoặc không đọc được) — lập lại kế hoạch."
        if st.st_size != e.get("size") or st.st_mtime_ns != e["mtime_ns"]:
            return f"'{e['src']}' đã đổi sau khi lập kế hoạch — lập lại kế hoạch."
    return None


# --------------------------------------------------------------------------
# plan_bulk_move
# --------------------------------------------------------------------------
def _ext_catalog() -> Dict[str, set]:
    import json
    ext: Dict[str, set] = {}
    try:
        data = json.loads(Path(find_systems_catalog()).read_text(encoding="utf-8"))
        for s in data.get("systems", []):
            folder = (s.get("folder") or s.get("id") or "").upper()
            ext.setdefault(folder, set()).update(e.lower() for e in s.get("extensions", []))
    except (OSError, ValueError):
        pass
    return ext


def _plan_game(rom_paths: List[str], name: str, primary_path, secondary_path, dest_card) -> Dict[str, Any]:
    """plan_move cho MỘT game có thể gồm nhiều .cue (nhiều đĩa không .m3u): gộp kế hoạch từng đĩa.
    Một đĩa bị từ chối -> cả game bị từ chối (không chuyển nửa game)."""
    plans = [plan_move(p, primary_path, secondary_path, dest_card) for p in rom_paths]
    if len(plans) == 1:
        return plans[0]
    bad = next((p for p in plans if not p["ok"]), None)
    if bad is not None:
        return bad
    files: Dict[str, Dict[str, Any]] = {}
    for p in plans:
        for e in p["files"]:
            k = _key(e["src"])
            if k in files:
                files[k]["remove_src"] = files[k]["remove_src"] and e["remove_src"]
            else:
                files[k] = dict(e)
    entries = sorted(files.values(), key=lambda e: e["src"])
    warnings = list(dict.fromkeys(w for p in plans for w in p["warnings"]))
    out = dict(plans[0], files=entries, warnings=warnings, game=name,
               bytes=sum(e["size"] for e in entries),
               bytes_freed=sum(e["size"] for e in entries if e["remove_src"]))
    n_rom = sum(1 for e in entries if e["kind"] in ("rom", "track", "playlist"))
    out["message"] = (f"Sẽ chuyển '{name}' ({len(plans)} đĩa) từ {out['source_card']} sang {dest_card}: {n_rom} file ROM, "
                      f"{len(entries) - n_rom} file đi kèm, giải phóng {out['bytes_freed'] / MB:.1f} MB ở thẻ nguồn.")
    return out


def plan_bulk_move(inventory, primary_path, secondary_path, dest_card,
                   target_free_mb: Optional[int] = None, system: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": False, "moves": [], "skipped": [], "reached": False, "warnings": [],
                           "message": "", "source_card": None, "dest_card": dest_card,
                           "target_free_mb": target_free_mb, "bytes_freed": 0, "bytes_copy": 0,
                           "free_before_mb": None, "free_after_mb": None}
    if dest_card not in ("primary", "secondary"):
        out["message"] = "Từ chối: dest_card phải là 'primary' hoặc 'secondary'."
        return out
    if target_free_mb is None or target_free_mb <= 0:
        out["message"] = "Cần target_free_mb > 0 (số MB trống mong muốn ở thẻ nguồn)."
        return out
    src_id = "secondary" if dest_card == "primary" else "primary"
    out["source_card"] = src_id
    roots = _roots(primary_path, secondary_path)
    src_root, dest_root = roots[src_id], roots[dest_card]
    card = (inventory or {}).get(src_id)
    if not card or src_root is None or dest_root is None:
        out["message"] = "Thiếu thẻ nguồn hoặc thẻ đích — cần cả hai thẻ để chuyển game."
        return out

    roms = card["roms"]
    index = {_key(r["absolute_path"]): Path(r["absolute_path"]) for r in roms}
    size_of = {_key(r["absolute_path"]): r["size"] for r in roms}
    protected = set()
    for r in roms:
        if r["extension"] in PLAYLIST_EXTS:
            protected.update(_key(f) for f in _expand(Path(r["absolute_path"]), index)[0])
    exts = _ext_catalog()
    art_size = {(canon_system(i["system"]), i["stem"].lower()): i["size"] for i in card.get("images", [])}
    save_info: Dict[str, List[Dict]] = {}
    for sv in card.get("saves", []) + card.get("states", []):
        s = sv.get("stem", "").lower()
        save_info.setdefault(s, []).append(sv)
        save_info.setdefault(re.sub(r"\.state.*$", "", s), []).append(sv)
    now = time.time()
    want_sys = canon_system(system) if system else None

    # Game nhiều đĩa không có .m3u: gom các .cue "Tên (Disc N)" cùng thư mục thành MỘT đơn vị game.
    disc_groups: Dict[tuple, Dict[int, List[Dict]]] = {}
    for r in roms:
        if r["extension"] != ".cue" or _key(r["absolute_path"]) in protected:
            continue
        m = DISC_RE.match(r["stem"])
        if m:
            gk = (os.path.normcase(str(Path(r["absolute_path"]).parent)), canon_system(r["system"]),
                  m.group(1).strip().lower())
            disc_groups.setdefault(gk, {}).setdefault(int(m.group(2)), []).append(r)
    group_of: Dict[str, List[Dict]] = {}     # key của .cue -> các .cue của cùng game (sắp theo số đĩa)
    group_title: Dict[str, str] = {}
    for gk, by_num in disc_groups.items():
        if len(by_num) < 2:
            continue
        members = [r for n in sorted(by_num) for r in sorted(by_num[n], key=lambda x: x["absolute_path"])]
        title = DISC_RE.match(members[0]["stem"]).group(1).strip()
        for r in members:
            group_of[_key(r["absolute_path"])] = members
            group_title[_key(r["absolute_path"])] = title

    cands = []
    for r in roms:
        k = _key(r["absolute_path"])
        if k in protected or r["extension"] in (".gdi", ".ccd") or r["size"] <= 0:
            continue
        members = group_of.get(k)
        if members is not None and members[0] is not r:
            continue                       # đĩa 2.. đi theo ứng viên của đĩa đầu
        cs = canon_system(r["system"])
        if want_sys and cs != want_sys:
            continue
        if r["extension"] not in exts.get(cs, set()) and r["extension"] not in PLAYLIST_EXTS:
            continue
        members = members or [r]
        parts, stems, freed = [], set(), 0
        for mr in members:
            mp = _expand(Path(mr["absolute_path"]), index)[0] if mr["extension"] in PLAYLIST_EXTS else []
            parts.append(mp)
            stems |= {mr["stem"].lower()} | {f.stem.lower() for f in mp}
            freed += mr["size"] + sum(size_of.get(_key(f), 0) for f in mp) + art_size.get((cs, mr["stem"].lower()), 0)
        svs = [sv for s_ in stems for sv in save_info.get(s_, [])]
        if len(members) > 1:
            name = group_title[k]
        elif r["extension"] in PLAYLIST_EXTS:
            name = r["stem"]
        else:
            name = r["name"]
        cands.append({"rom": r, "members": members, "name": name, "freed": freed,
                      "copy": freed + sum(sv["size"] for sv in svs),
                      "has_save": bool(svs),
                      "recent": any(now - sv.get("mtime", 0) < RECENT_SAVE_SECS for sv in svs)})

    free_before = _free_bytes(src_root)
    out["free_before_mb"] = free_before // MB
    need = target_free_mb * MB - free_before
    if need <= 0:
        out.update(ok=True, reached=True, free_after_mb=free_before // MB,
                   message=f"Thẻ nguồn đã trống {free_before // MB} MB >= {target_free_mb} MB — không cần chuyển.")
        return out

    recent = [c for c in cands if c["recent"]]
    if recent:
        out["warnings"].append(f"Bỏ qua {len(recent)} game có save/state sửa trong {RECENT_SAVE_SECS // 60} phút qua "
                               "(coi là đang chơi). # CHƯA KIỂM CHỨNG: không có cách chắc chắn biết game đang mở.")
    pool = [c for c in cands if not c["recent"]]
    dest_budget = _free_bytes(dest_root) - MIN_FREE_AFTER
    freed = copy = 0
    while freed < need and pool:
        remaining = need - freed
        fits = [c for c in pool if c["freed"] >= remaining]
        # Vừa đủ thì lấy game nhỏ nhất đủ lớn (đừng chuyển 2 GB để lấy 100 MB); chưa đủ thì lấy game lớn nhất.
        pick = (min(fits, key=lambda c: (c["has_save"], c["freed"], c["rom"]["absolute_path"])) if fits else
                min(pool, key=lambda c: (c["has_save"], -c["freed"], c["rom"]["absolute_path"])))
        pool.remove(pick)
        r = pick["rom"]
        pl = _plan_game([m["absolute_path"] for m in pick["members"]], pick["name"],
                        primary_path, secondary_path, dest_card)
        if not pl["ok"]:
            out["skipped"].append({"rom_path": r["absolute_path"], "reason": pl["message"]})
            continue
        if copy + pl["bytes"] > dest_budget:
            out["skipped"].append({"rom_path": r["absolute_path"],
                                   "reason": "Thẻ đích không đủ chỗ cho game này (phải còn >= 200 MB)."})
            continue
        out["moves"].append({"rom_path": r["absolute_path"], "name": pick["name"], "system": r["system"],
                             "bytes_freed": pl["bytes_freed"], "bytes_copy": pl["bytes"],
                             "has_save": pick["has_save"], "discs": len(pick["members"]), "plan": pl})
        freed += pl["bytes_freed"]
        copy += pl["bytes"]

    out["bytes_freed"], out["bytes_copy"] = freed, copy
    out["free_after_mb"] = (free_before + freed) // MB
    out["reached"] = freed >= need
    out["ok"] = True
    out["message"] = (f"Chuyển {len(out['moves'])} game sang {dest_card}: giải phóng {freed / MB:.0f} MB, thẻ nguồn "
                      f"từ {free_before // MB} MB lên {out['free_after_mb']} MB trống"
                      + ("." if out["reached"] else f" — CHƯA đạt mục tiêu {target_free_mb} MB (hết game phù hợp)."))
    return out
