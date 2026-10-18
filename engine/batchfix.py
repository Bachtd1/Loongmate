"""
Sửa hàng loạt một chạm cho LoongMate (plan -> xác nhận -> execute -> undo).

API
---
plan_batch_fix(inventory, audit_report, primary_path, secondary_path, catalog) -> dict
    CHỈ ĐỌC. `audit_report` là kết quả LibraryDoctor.audit(); `catalog` là
    LibraryDoctor.systems_catalog. Trả về
    {"steps": [{"id","kind","label","detail","files_affected","risk": low|medium,
                "reversible": bool, "data": {...}}], "summary": str,
     "primary_path", "secondary_path"}
    kind: create_dirs | fix_cue | create_m3u | legacy_folder

execute_batch_fix(plan, app_data, selected_kinds=None) -> dict
    {"done": [...], "refused": [...], "journal": "<app_data>/batchfix-journal.jsonl", "batch_id"}
    Thứ tự thực hiện cố định: create_dirs, fix_cue, create_m3u, legacy_folder (gộp thư mục
    chạy cuối cùng, và quét lại thư mục nguồn lúc chạy nên m3u/cue vừa tạo cũng được chuyển theo).
    Mọi đường dẫn trong plan được kiểm lại là nằm trong Roms/ Images/ của một thẻ — plan bị sửa
    tay vẫn không thoát được vùng cho phép.

undo_batch_fix(journal_path, batch_id=None) -> dict
    Hoàn tác lô gần nhất chưa undo (hoặc batch_id) theo thứ tự ngược:
    {"undone": [...], "skipped": [{"op","path","reason"}], "batch_id"}
    Trạng thái thật trên đĩa quyết định việc hoàn tác, không tin mù quáng vào journal.

Quy tắc gộp thư mục legacy (rủi ro cao vì đổi đường dẫn game): đích chưa có file cùng tên ->
os.rename (cùng thẻ); trùng tên + cùng nội dung (SHA-256) -> bản nguồn vào Quarantine; trùng tên
khác nội dung -> BỎ QUA, nguồn giữ nguyên. Game nhiều file (.cue/.m3u + track) là một nhóm: nếu một
file của nhóm bị bỏ qua thì cả nhóm đứng yên, để không tách cue khỏi bin. Thư mục alias chỉ bị xoá
khi đã rỗng hoàn toàn. Race nhỏ: os.rename trên POSIX ghi đè nếu đích xuất hiện giữa lúc kiểm
tra và lúc đổi tên (cửa sổ micro-giây, chỉ khi có tiến trình khác ghi đúng file đó).

Journal (jsonl, fsync trước khi hành động): mỗi dòng {"batch_id","ts","op",...}; op thuộc
mkdir | rmdir | rename | quarantine | write_m3u | fix_cue | undone. Sửa cue luôn sao lưu bản gốc
thành "<tên>.cue.bak-loongmate" trước (từ chối nếu bản sao lưu đã tồn tại).
"""

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from engine.dedupe import _key, _read_refs, _update_quarantine_index
from engine.inventory import compute_sha256, is_safe_relative_path

JOURNAL_NAME = "batchfix-journal.jsonl"
BACKUP_SUFFIX = ".bak-loongmate"
STD_DIRS = {"Roms", "Images", "Saves", "States", "BIOS"}
KIND_ORDER = ["create_dirs", "fix_cue", "create_m3u", "legacy_folder", "move_misplaced"]
DISC_RE = re.compile(r"^(.*?)\s*\(Disc\s*([0-9]+)\)", re.IGNORECASE)
# Ưu tiên file đại diện cho một đĩa trong .m3u: cue/chd... hơn là track .bin rời.
DISC_EXT_PRIORITY = [".cue", ".chd", ".pbp", ".gdi", ".ccd", ".iso", ".img", ".mdf", ".bin"]


# ---------------------------------------------------------------- helpers
def _roots(plan) -> List[Path]:
    return [Path(p).resolve() for p in (plan.get("primary_path"), plan.get("secondary_path")) if p]


def _card_of(roots: List[Path], p: Path) -> Optional[Path]:
    for r in roots:
        if is_safe_relative_path(r, p):
            return r
    return None


def _in_section(roots, p: Path, sections=("Roms",)) -> Optional[Path]:
    for r in roots:
        for s in sections:
            if is_safe_relative_path(r / s, p):
                return r
    return None


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _Journal:
    def __init__(self, path: Path, batch_id: str):
        self.path, self.batch_id = path, batch_id
        # File .cue/.m3u mà CHÍNH lô này vừa sửa/tạo: kế hoạch đã duyệt (dấu thời gian) không thể biết trước chúng.
        self.touched = set()

    def add(self, op: str, **kw) -> None:
        if op in ("fix_cue", "write_m3u") and kw.get("path"):
            self.touched.add(_key(kw["path"]))
        entry = {"batch_id": self.batch_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "op": op, **kw}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as f:
            f.write((json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())


def _read_journal(path: Path) -> List[Dict[str, Any]]:
    out = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def _mkdirs(target: Path, j: _Journal) -> None:
    """Tạo thư mục kèm các cấp thiếu, ghi journal từng cấp thật sự được tạo."""
    missing = []
    p = target
    while not p.exists():
        missing.append(p)
        p = p.parent
    for d in reversed(missing):
        j.add("mkdir", path=str(d))
        d.mkdir(exist_ok=True)


# ---------------------------------------------------------------- plan
def pick_disc_files(roms: List[Dict[str, Any]], title: str) -> Optional[Dict[int, str]]:
    """Mỗi SỐ đĩa -> tên file đại diện, trong danh sách ROM của MỘT thư mục; None nếu không rõ ràng
    (một đĩa có hai file đại diện cùng độ ưu tiên). Tiêu chí dùng chung với LibraryDoctor."""
    by_disc: Dict[int, List[Dict[str, Any]]] = {}
    for r in roms:
        m = DISC_RE.match(r["stem"])
        if m and m.group(1).strip().lower() == title.lower() and r["extension"] in DISC_EXT_PRIORITY:
            by_disc.setdefault(int(m.group(2)), []).append(r)
    chosen = {}
    for n, items in by_disc.items():
        best = min(DISC_EXT_PRIORITY.index(i["extension"]) for i in items)
        top = [i for i in items if DISC_EXT_PRIORITY.index(i["extension"]) == best]
        if len(top) != 1:
            return None
        chosen[n] = top[0]["name"]
    return chosen


def find_multidisc_games(roms_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Game đa đĩa THẬT trong danh sách ROM: cùng hệ, cùng thư mục, cùng tên game, có >= 2 SỐ đĩa
    khác nhau và mỗi đĩa chọn được đúng một file đại diện. Một đĩa nhiều track (.cue + .bin cùng
    "(Disc 1)") KHÔNG phải đa đĩa. Trả [{"system","title","folder","discs": {số_đĩa: tên_file}}],
    sắp theo (hệ, thư mục, tên). Doctor và plan_batch_fix cùng dùng hàm này."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    labels: Dict[tuple, tuple] = {}
    for r in roms_list:
        m = DISC_RE.match(r["stem"])
        if not m:
            continue
        title = m.group(1).strip()
        folder = str(Path(r["absolute_path"]).parent)
        key = (r["system"].upper(), os.path.normcase(folder), title.lower())
        groups.setdefault(key, []).append(r)
        labels.setdefault(key, (r["system"].upper(), folder, title))
    out = []
    for key in sorted(groups):
        system, folder, title = labels[key]
        discs = pick_disc_files(groups[key], title)
        if discs and len(discs) >= 2:
            out.append({"system": system, "title": title, "folder": folder, "discs": discs})
    return out


def _disc_files(inventory, folder: str, title: str) -> Optional[Dict[int, str]]:
    """Mỗi số đĩa -> tên file đại diện trong `folder`; None nếu không rõ ràng."""
    roms = []
    for cid in ("primary", "secondary"):
        card = inventory.get(cid)
        for r in (card or {}).get("roms", []):
            if os.path.normcase(str(Path(r["absolute_path"]).parent)) == os.path.normcase(folder):
                roms.append(r)
    return pick_disc_files(roms, title)


def plan_batch_fix(inventory, audit_report, primary_path, secondary_path, catalog) -> Dict[str, Any]:
    roots = {"primary": Path(primary_path) if primary_path else None,
             "secondary": Path(secondary_path) if secondary_path else None}
    steps: List[Dict[str, Any]] = []

    def add(kind, label, detail, n, risk, data):
        steps.append({"id": f"{kind}-{len(steps) + 1}", "kind": kind, "label": label, "detail": detail,
                      "files_affected": n, "risk": risk, "reversible": True, "data": data})

    actions = audit_report.get("fixable_actions", [])
    issues = audit_report.get("issues", [])

    # create_dirs: dùng lại action create_dir của doctor.
    for a in actions:
        if a.get("type") == "create_dir":
            add("create_dirs", a.get("label", f"Tạo thư mục {a['path']}"),
                f"Tạo thư mục chuẩn còn thiếu: {a['path']}", 0, "low", {"path": a["path"]})

    # fix_cue: chỉ action fix_cue của doctor (case mismatch / self-named: đều có đúng một ánh xạ).
    cues: Dict[str, List[Dict[str, str]]] = {}
    for a in actions:
        if a.get("type") == "fix_cue":
            rep = {"old": a["old_str"], "new": a["new_str"]}
            lst = cues.setdefault(a["path"], [])
            if rep not in lst:
                lst.append(rep)
    for path, reps in cues.items():
        name = Path(path).name
        add("fix_cue", f"Sửa tham chiếu trong {name}",
            "; ".join(f"'{r['old']}' -> '{r['new']}'" for r in reps) +
            f". Bản gốc được sao lưu thành {name}{BACKUP_SUFFIX} trước khi sửa.",
            1, "low", {"path": path, "replacements": reps})

    # create_m3u: dùng lại phát hiện MULTIDISC_NO_M3U của doctor, nhưng dựng danh sách đĩa
    # theo SỐ đĩa (Disc 10 sau Disc 2) và chỉ lấy file đại diện mỗi đĩa (không lẫn .bin track).
    for iss in issues:
        if iss.get("action_type") != "create_m3u" or not iss.get("m3u_name"):
            continue
        folder, m3u_name = iss["folder"], iss["m3u_name"]
        discs = _disc_files(inventory, folder, m3u_name[:-4])
        if not discs or len(discs) < 2:
            continue  # cảnh báo của doctor có thể là dương tính giả (1 đĩa nhiều track) -> không tạo
        lines = [discs[n] for n in sorted(discs)]
        add("create_m3u", f"Tạo playlist {m3u_name}", f"Gom {len(lines)} đĩa: {', '.join(lines)}",
            len(lines), "low", {"folder": folder, "name": m3u_name, "lines": lines})

    # legacy_folder: Roms/<alias> và Images/<alias> -> thư mục chuẩn.
    seen = set()
    for cid in ("primary", "secondary"):
        card, root = inventory.get(cid), roots[cid]
        if not card or root is None:
            continue
        for section, key in (("Roms", "roms"), ("Images", "images")):
            counts: Dict[str, int] = {}
            for r in card.get(key, []):
                counts[r["system"]] = counts.get(r["system"], 0) + 1
            for dirname, n in sorted(counts.items()):
                info = catalog.get(dirname.upper())
                canon = (info or {}).get("folder")
                if not canon or canon.upper() == dirname.upper() or (cid, section, dirname) in seen:
                    continue
                seen.add((cid, section, dirname))
                add("legacy_folder", f"Gộp {section}/{dirname} vào {section}/{canon} ({cid})",
                    f"{n} file đổi đường dẫn từ {section}/{dirname}/ sang {section}/{canon}/. "
                    f"Trùng tên cùng nội dung -> Quarantine; trùng tên khác nội dung -> bỏ qua.",
                    n, "medium", {"card_root": str(root), "section": section, "alias": dirname,
                                  "canonical": canon})
    # move_misplaced: file đúng định dạng nhưng nằm trong thư mục mà Leaf không liệt kê đuôi đó (doctor: INVALID_EXT có
    # "move_to", vd. .gbc trong Roms/GB -> Roms/GBC). Cùng thẻ, kèm ảnh bìa cùng tên; save/state không đổi (Leaf xếp theo core).
    for iss in issues:
        to, src = iss.get("move_to"), iss.get("file_path")
        if not to or not src or Path(to).name != to:
            continue
        src_p = Path(src)
        card = next((r for r in roots.values() if r is not None and is_safe_relative_path(r / "Roms", src_p)), None)
        if card is None:
            continue
        dst = card / "Roms" / to / src_p.name
        art = []
        img_dir = card / "Images" / src_p.parent.name
        try:
            for a in sorted(img_dir.iterdir()) if img_dir.is_dir() else ():
                if a.is_file() and a.stem == src_p.stem:
                    art.append([str(a), str(card / "Images" / to / a.name)])
        except OSError:
            pass
        add("move_misplaced", f"Chuyển {src_p.name} sang Roms/{to}",
            f"Leaf không hiện file này trong Roms/{src_p.parent.name}/; chuyển sang Roms/{to}/ cùng thẻ"
            + (f" (kèm {len(art)} ảnh bìa)" if art else "") + ". Save/state giữ nguyên chỗ cũ (xếp theo core).",
            1 + len(art), "low", {"card_root": str(card), "src": str(src_p), "dst": str(dst), "art": art})

    n_by = {k: sum(1 for s in steps if s["kind"] == k) for k in KIND_ORDER}
    summary = (f"{len(steps)} bước: " + ", ".join(f"{k} x{v}" for k, v in n_by.items() if v)
               if steps else "Không có gì để sửa tự động.")
    return {"steps": steps, "summary": summary,
            "primary_path": str(primary_path) if primary_path else None,
            "secondary_path": str(secondary_path) if secondary_path else None}


def stamp_plan(plan) -> Dict[str, Any]:
    """Bản sao của plan kèm "dấu" để execute_batch_fix chỉ làm ĐÚNG những gì đã duyệt (CHỈ ĐỌC):
    fix_cue: data["sha256_before"] = sha256 của cue lúc duyệt; legacy_folder: data["files"] = [[rel_posix, size,
    mtime_ns], ...] mọi file (không ẩn) trong thư mục alias lúc duyệt. Bước bị thiếu file/không đọc được thì
    để nguyên không dấu — khi đó execute vẫn dùng các chốt sẵn có."""
    steps = []
    for st in plan.get("steps", []):
        st = {**st, "data": dict(st.get("data") or {})}
        d = st["data"]
        try:
            if st.get("kind") == "fix_cue":
                d["sha256_before"] = _sha(Path(d["path"]).read_bytes())
            elif st.get("kind") == "move_misplaced":
                stt = Path(d["src"]).stat()
                d["stamp"] = [stt.st_size, stt.st_mtime_ns]
            elif st.get("kind") == "legacy_folder":
                src = Path(d["card_root"]) / d["section"] / d["alias"]
                rows = []
                for dp, _dns, fns in os.walk(src, followlinks=False):
                    for fn in fns:
                        p = Path(dp) / fn
                        if fn.startswith(".") or p.is_symlink():
                            continue
                        stt = p.stat()
                        rows.append([p.relative_to(src).as_posix(), stt.st_size, stt.st_mtime_ns])
                d["files"] = sorted(rows)
        except (OSError, KeyError):
            pass
        steps.append(st)
    return {**plan, "steps": steps}


def pending_batch(journal_path) -> Dict[str, Any]:
    """Lô nào undo_batch_fix sẽ hoàn tác nếu gọi ngay (CHỈ ĐỌC, theo nhật ký; việc hoàn tác thật còn tuỳ trạng
    thái đĩa): {"has_batch", "batch_id", "created_at", "operations", "by_op"}."""
    entries = _read_journal(Path(journal_path))
    undone = {e["batch_id"] for e in entries if e.get("op") == "undone"}
    ids = [e["batch_id"] for e in entries if e.get("op") != "undone" and e["batch_id"] not in undone]
    if not ids:
        return {"has_batch": False, "batch_id": None, "created_at": None, "operations": 0, "by_op": {}}
    bid = ids[-1]
    mine = [e for e in entries if e["batch_id"] == bid and e.get("op") != "undone"]
    by_op: Dict[str, int] = {}
    for e in mine:
        by_op[e["op"]] = by_op.get(e["op"], 0) + 1
    return {"has_batch": True, "batch_id": bid, "created_at": mine[0].get("ts"), "operations": len(mine), "by_op": by_op}


# ---------------------------------------------------------------- execute
def _do_create_dir(step, roots, j, res):
    p = Path(step["data"]["path"])
    card = _card_of(roots, p)
    if card is None or p.parent.resolve() != card or p.name not in STD_DIRS:
        res["refused"].append({"kind": "create_dirs", "path": str(p), "reason": "Không phải thư mục chuẩn của thẻ."})
        return
    if p.exists():
        res["refused"].append({"kind": "create_dirs", "path": str(p), "reason": "Thư mục đã tồn tại."})
        return
    _mkdirs(p, j)
    res["done"].append({"kind": "create_dirs", "label": step["label"], "path": str(p)})


def _do_move_misplaced(step, roots, j, res):
    d = step["data"]
    card = Path(d["card_root"]).resolve()
    src, dst = Path(d["src"]), Path(d["dst"])
    ref = lambda why, p=src: res["refused"].append({"kind": "move_misplaced", "path": str(p), "reason": why})
    if card not in roots or not is_safe_relative_path(card / "Roms", src) or not is_safe_relative_path(card / "Roms", dst)             or dst.parent.parent.resolve() != (card / "Roms").resolve():
        return ref("Đường dẫn ngoài Roms/ của thẻ.")
    if src.is_symlink() or not src.is_file():
        return ref("File nguồn không còn.")
    if d.get("stamp") is not None:
        stt = src.stat()
        if [stt.st_size, stt.st_mtime_ns] != d["stamp"]:
            return ref("File đã đổi từ lúc duyệt kế hoạch — bỏ qua.")
    if dst.exists():
        return ref(f"Đã có {dst.name} trong Roms/{dst.parent.name} — không ghi đè.", dst)
    _mkdirs(dst.parent, j)
    j.add("rename", src=str(src), dst=str(dst))
    os.rename(src, dst)
    moved_art = 0
    for a_src, a_dst in d.get("art") or []:
        a_src, a_dst = Path(a_src), Path(a_dst)
        if not is_safe_relative_path(card / "Images", a_src) or not is_safe_relative_path(card / "Images", a_dst)                 or not a_src.is_file() or a_dst.exists():
            continue
        _mkdirs(a_dst.parent, j)
        j.add("rename", src=str(a_src), dst=str(a_dst))
        os.rename(a_src, a_dst)
        moved_art += 1
    res["done"].append({"kind": "move_misplaced", "label": step["label"], "path": str(dst), "art_moved": moved_art})


def _cue_pattern(old: str) -> "re.Pattern":
    """FILE "old" (có ngoặc kép) HOẶC FILE old (không ngoặc kép, kết thúc bằng khoảng trắng/hết dòng)."""
    o = re.escape(old.encode("utf-8"))
    return re.compile(rb'(FILE\s+)(?:"' + o + rb'"|' + o + rb'(?=[ \t\r\n]|$))', re.IGNORECASE | re.MULTILINE)


def apply_cue_fix(cue, replacements: List[Dict[str, str]], roots: List[Path],
                  journal: Optional["_Journal"] = None) -> Dict[str, Any]:
    """Sửa tham chiếu FILE trong một .cue. Dùng chung cho execute_batch_fix và LibraryDoctor.execute_fix.

    replacements: [{"old","new"}]. Đọc/ghi BYTES (giữ nguyên CRLF và mọi byte không phải UTF-8 như
    TITLE latin-1). Nhận cả FILE "x" lẫn FILE x (không ngoặc kép; bản thay luôn được đặt trong ngoặc kép).
    Từ chối (ok=False, không ghi gì) khi: .cue ngoài Roms/ của `roots`, không phải file thường, đã có
    bản sao lưu, đích mới không có cạnh file cue, không thấy dòng FILE cần sửa, hoặc nội dung không đổi.
    Trước khi ghi: sao lưu bản gốc thành "<tên>.cue.bak-loongmate", rồi ghi tạm + os.replace.
    Trả {"ok","reason","path","backup"}."""
    cue = Path(cue)
    fail = lambda why: {"ok": False, "reason": why, "path": str(cue), "backup": None}
    if _in_section(roots, cue) is None or cue.suffix.lower() != ".cue" or not cue.is_file() or cue.is_symlink():
        return fail("Không phải file .cue thường trong Roms/ của thẻ.")
    bak = cue.with_name(cue.name + BACKUP_SUFFIX)
    if bak.exists():
        return fail(f"Đã có bản sao lưu {bak.name}; không ghi đè bản gốc cũ. Hãy kiểm tra thủ công.")
    data = cue.read_bytes()
    new = data
    for r in replacements:
        if not (cue.parent / r["new"]).is_file():
            return fail(f"Đích '{r['new']}' không tồn tại cạnh file cue.")
        new, n = _cue_pattern(r["old"]).subn(
            lambda m, nw=r["new"].encode("utf-8"): m.group(1) + b'"' + nw + b'"', new)
        if n == 0:
            return fail(f"Không thấy dòng FILE \"{r['old']}\" trong cue nên không sửa.")
    if new == data:
        return fail("Nội dung không đổi.")
    if journal is not None:
        journal.add("fix_cue", path=str(cue), backup=str(bak), sha_before=_sha(data), sha_after=_sha(new))
    with open(bak, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp = cue.with_name(cue.name + ".partial")
    with open(tmp, "wb") as f:
        f.write(new)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, cue)
    return {"ok": True, "reason": None, "path": str(cue), "backup": str(bak)}


def _do_fix_cue(step, roots, j, res):
    d = step["data"]
    want = d.get("sha256_before")   # chỉ có khi kế hoạch được duyệt (stamp_plan): cue phải còn nguyên như lúc duyệt
    if want is not None:
        try:
            now = _sha(Path(d["path"]).read_bytes())
        except OSError as e:
            res["refused"].append({"kind": "fix_cue", "path": d["path"], "reason": f"Không đọc được cue: {e}"})
            return
        if now != want:
            res["refused"].append({"kind": "fix_cue", "path": d["path"],
                                   "reason": "File .cue đã đổi sau khi lập kế hoạch — không sửa. Lập lại kế hoạch."})
            return
    r = apply_cue_fix(d["path"], d["replacements"], roots, j)
    if r["ok"]:
        res["done"].append({"kind": "fix_cue", "label": step["label"], "path": r["path"], "backup": r["backup"]})
    else:
        res["refused"].append({"kind": "fix_cue", "path": r["path"], "reason": r["reason"]})


def _do_create_m3u(step, roots, j, res):
    d = step["data"]
    folder = Path(d["folder"])
    m3u = folder / d["name"]
    ref = lambda why: res["refused"].append({"kind": "create_m3u", "path": str(m3u), "reason": why})
    if _in_section(roots, folder) is None or not folder.is_dir():
        return ref("Thư mục không nằm trong Roms/ của thẻ.")
    if Path(d["name"]).name != d["name"] or not d["name"].lower().endswith(".m3u"):
        return ref("Tên playlist không hợp lệ.")
    if m3u.exists():
        return ref("Đã có file cùng tên; không ghi đè.")
    for ln in d["lines"]:
        if Path(ln).name != ln or not (folder / ln).is_file():
            return ref(f"Đĩa '{ln}' không tồn tại trong thư mục.")
    content = ("\n".join(d["lines"]) + "\n").encode("utf-8")
    j.add("write_m3u", path=str(m3u), sha256=_sha(content))
    tmp = folder / (d["name"] + ".partial")
    with open(tmp, "wb") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, m3u)
    res["done"].append({"kind": "create_m3u", "label": step["label"], "path": str(m3u)})


def _quarantine_base(card: Path, primary: Optional[Path], app_data: Path) -> Path:
    return (app_data / "Quarantine") if primary is not None and card == primary else (card / ".userdata" / "LoongMate" / "Quarantine")


def _do_legacy(step, roots, j, res, app_data, ts, primary):
    d = step["data"]
    section, alias, canon = d["section"], d["alias"], d["canonical"]
    card = Path(d["card_root"]).resolve()
    ref = lambda why, p=None: res["refused"].append({"kind": "legacy_folder", "path": str(p or alias), "reason": why})
    if card not in roots or section not in ("Roms", "Images"):
        return ref("Thẻ/mục không hợp lệ.")
    if Path(alias).name != alias or Path(canon).name != canon or alias.upper() == canon.upper():
        return ref("Tên thư mục không hợp lệ.")
    src_dir, dst_dir = card / section / alias, card / section / canon
    if src_dir.is_symlink() or not src_dir.is_dir() or not is_safe_relative_path(card / section, src_dir):
        return ref("Thư mục nguồn không tồn tại hoặc nằm ngoài vùng cho phép.", src_dir)
    if dst_dir.is_symlink():
        return ref("Thư mục đích là symlink.", dst_dir)
    if not os.access(src_dir, os.W_OK):
        return ref("Thẻ đang read-only.", src_dir)

    # Kế hoạch đã duyệt (stamp_plan) liệt kê đúng những file được phép chuyển: file mới xuất hiện hoặc đã đổi
    # từ lúc duyệt thì đứng yên (trừ .cue/.m3u do chính lô này sửa/tạo).
    approved = {f[0]: [f[1], f[2]] for f in d["files"]} if d.get("files") is not None else None

    # Pass 1: quyết định từng file.
    files: Dict[str, Dict[str, Any]] = {}
    for dp, dns, fns in os.walk(src_dir, followlinks=False):
        for fn in fns:
            p = Path(dp) / fn
            rel = p.relative_to(src_dir)
            if fn.startswith("."):
                continue  # file ẩn: để nguyên (thư mục sẽ không bị xoá vì còn sót)
            if p.is_symlink():
                ref("Là symlink — không di chuyển.", p)
                continue
            dst = dst_dir / rel
            if approved is not None and _key(p) not in j.touched:
                want = approved.get(rel.as_posix())
                stt = p.stat()
                if want is None or want != [stt.st_size, stt.st_mtime_ns]:
                    files[_key(p)] = {"src": p, "dst": dst, "rel": rel, "status": "outside_plan"}
                    continue
            if not dst.exists():
                st = "move"
            elif dst.is_file() and dst.stat().st_size == p.stat().st_size and compute_sha256(dst) == compute_sha256(p):
                st = "dupe"
            else:
                st = "conflict"
            files[_key(p)] = {"src": p, "dst": dst, "rel": rel, "status": st}

    # Nhóm game nhiều file: một file bị bỏ qua -> cả nhóm đứng yên.
    parent = {k: k for k in files}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for k, f in files.items():
        if f["src"].suffix.lower() in (".cue", ".m3u"):
            for rp in _read_refs(f["src"]):
                rk = _key(rp)
                if rk in files:
                    parent[find(rk)] = find(k)
    bad_roots = {find(k) for k, f in files.items() if f["status"] in ("conflict", "outside_plan")}
    for k, f in files.items():
        if f["status"] == "outside_plan":
            ref("Không có trong kế hoạch đã duyệt (hoặc đã đổi sau khi lập kế hoạch) — bỏ qua, nguồn giữ nguyên.", f["src"])
        elif f["status"] == "conflict":
            ref("Đích đã có file cùng tên nhưng nội dung KHÁC — bỏ qua, nguồn giữ nguyên.", f["src"])
        elif find(k) in bad_roots:
            f["status"] = "group_skip"
            ref("Thuộc game có file khác bị bỏ qua — giữ nguyên cả nhóm để không tách cue khỏi track.", f["src"])

    # Pass 2: thực hiện.
    moved = quarantined = 0
    qbase = _quarantine_base(card, primary, app_data)
    q_index = []
    for k in sorted(files):
        f = files[k]
        src, dst = f["src"], f["dst"]
        try:
            if f["status"] == "move":
                _mkdirs(dst.parent, j)
                j.add("rename", src=str(src), dst=str(dst))
                if dst.exists():
                    ref("Đích vừa xuất hiện — bỏ qua.", src)
                    continue
                os.rename(src, dst)
                moved += 1
            elif f["status"] == "dupe":
                qdst = qbase / ts / src.relative_to(card)
                if qdst.exists():
                    ref(f"Đích Quarantine đã tồn tại: {qdst}", src)
                    continue
                j.add("quarantine", src=str(src), dst=str(qdst), sha256=compute_sha256(src))
                qdst.parent.mkdir(parents=True, exist_ok=True)
                size = src.stat().st_size
                shutil.move(str(src), str(qdst))
                q_index.append({"timestamp": ts, "original_path": str(src), "quarantine_path": str(qdst), "size": size})
                quarantined += 1
        except OSError as e:
            ref(f"Lỗi I/O: {e}", src)
    if q_index:
        try:
            _update_quarantine_index(app_data, q_index)
        except OSError:
            pass

    # Pass 3: xoá thư mục nguồn rỗng (từ lá lên gốc).
    for dp, dns, fns in os.walk(src_dir, topdown=False, followlinks=False):
        p = Path(dp)
        try:
            if not os.listdir(p):
                j.add("rmdir", path=str(p))
                os.rmdir(p)
        except OSError:
            pass
    leftover = src_dir.exists()
    res["done"].append({"kind": "legacy_folder", "label": step["label"], "moved": moved,
                        "quarantined_duplicates": quarantined,
                        "skipped": sum(1 for f in files.values() if f["status"] in ("conflict", "group_skip", "outside_plan")),
                        "source_folder_removed": not leftover})
    if leftover:
        ref("Thư mục alias còn sót file nên được giữ lại (không xoá).", src_dir)


def execute_batch_fix(plan, app_data, selected_kinds=None) -> Dict[str, Any]:
    app_data = Path(app_data)
    journal_path = app_data / JOURNAL_NAME
    ts = time.strftime("%Y%m%d_%H%M%S")
    batch_id = f"{ts}-{os.getpid()}"
    res: Dict[str, Any] = {"done": [], "refused": [], "journal": str(journal_path), "batch_id": batch_id}
    roots = _roots(plan)
    if not roots:
        res["refused"].append({"kind": "*", "path": "", "reason": "Plan thiếu đường dẫn thẻ."})
        return res
    primary = Path(plan["primary_path"]).resolve() if plan.get("primary_path") else None
    j = _Journal(journal_path, batch_id)
    handlers = {"create_dirs": _do_create_dir, "fix_cue": _do_fix_cue, "create_m3u": _do_create_m3u,
                "move_misplaced": _do_move_misplaced}
    for kind in KIND_ORDER:
        if selected_kinds is not None and kind not in selected_kinds:
            continue
        for step in [s for s in plan.get("steps", []) if s.get("kind") == kind]:
            try:
                if kind == "legacy_folder":
                    _do_legacy(step, roots, j, res, app_data, ts, primary)
                else:
                    handlers[kind](step, roots, j, res)
            except (OSError, KeyError, ValueError) as e:
                res["refused"].append({"kind": kind, "path": step.get("label", ""), "reason": f"Lỗi: {e}"})
    return res


# ---------------------------------------------------------------- undo
def undo_batch_fix(journal_path, batch_id: Optional[str] = None) -> Dict[str, Any]:
    journal_path = Path(journal_path)
    entries = _read_journal(journal_path)
    undone_ids = {e["batch_id"] for e in entries if e.get("op") == "undone"}
    if batch_id is None:
        ids = [e["batch_id"] for e in entries if e.get("op") != "undone" and e["batch_id"] not in undone_ids]
        if not ids:
            return {"undone": [], "skipped": [], "batch_id": None}
        batch_id = ids[-1]
    res: Dict[str, Any] = {"undone": [], "skipped": [], "batch_id": batch_id}
    if batch_id in undone_ids:
        res["skipped"].append({"op": "*", "path": "", "reason": "Lô này đã được hoàn tác."})
        return res
    skip = lambda op, path, why: res["skipped"].append({"op": op, "path": str(path), "reason": why})
    for e in reversed([x for x in entries if x["batch_id"] == batch_id]):
        op = e["op"]
        try:
            if op == "rmdir":
                p = Path(e["path"])
                if not p.exists():
                    p.mkdir(parents=True)
                    res["undone"].append({"op": op, "path": str(p)})
            elif op == "rename":
                src, dst = Path(e["src"]), Path(e["dst"])
                if not dst.exists():
                    continue  # chưa từng xảy ra
                if src.exists():
                    skip(op, src, "Đường dẫn cũ đã bị chiếm — không ghi đè.")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                os.rename(dst, src)
                res["undone"].append({"op": op, "path": str(src)})
            elif op == "quarantine":
                src, q = Path(e["src"]), Path(e["dst"])
                if not q.exists():
                    skip(op, q, "Bản trong Quarantine không còn (đã dọn?).")
                    continue
                if src.exists():
                    skip(op, src, "Đường dẫn cũ đã bị chiếm — không ghi đè.")
                    continue
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(q), str(src))
                res["undone"].append({"op": op, "path": str(src)})
            elif op == "write_m3u":
                p = Path(e["path"])
                if not p.exists():
                    continue
                if _sha(p.read_bytes()) != e["sha256"]:
                    skip(op, p, "Playlist đã bị sửa sau khi tạo — giữ lại.")
                    continue
                os.remove(p)
                res["undone"].append({"op": op, "path": str(p)})
            elif op == "fix_cue":
                p, bak = Path(e["path"]), Path(e["backup"])
                if not bak.exists():
                    skip(op, p, "Không còn bản sao lưu.")
                    continue
                cur = _sha(p.read_bytes()) if p.exists() else None
                if cur == e["sha_after"]:
                    os.replace(bak, p)
                    res["undone"].append({"op": op, "path": str(p)})
                elif cur == e["sha_before"]:
                    os.remove(bak)  # cue chưa bị sửa (intent có nhưng chưa ghi) -> chỉ dọn bản sao lưu
                else:
                    skip(op, p, "Cue đã bị sửa sau lô này — không ghi đè bằng bản sao lưu.")
            elif op == "mkdir":
                p = Path(e["path"])
                if p.exists():
                    try:
                        os.rmdir(p)
                        res["undone"].append({"op": op, "path": str(p)})
                    except OSError:
                        skip(op, p, "Thư mục không rỗng — giữ lại.")
        except OSError as ex:
            skip(op, e.get("path") or e.get("src") or "", f"Lỗi I/O: {ex}")
    _Journal(journal_path, batch_id).add("undone")
    return res
