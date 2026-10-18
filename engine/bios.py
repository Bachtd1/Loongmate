"""
Kiểm tra BIOS bằng md5 cho LoongMate. CHỈ ĐỌC: không ghi, không tải BIOS.

API
---
check_bios(primary_path, secondary_path, catalog, active_systems, hashes_path=None) -> dict
    catalog        : bản đồ của LibraryDoctor.systems_catalog (KHOÁ IN HOA -> thông tin hệ máy,
                     trường "bios" là danh sách tên file BIOS bắt buộc).
    active_systems : tên các thư mục hệ máy đang có ROM (bất kỳ alias nào, không phân biệt hoa/thường).
    Trả về {"systems": [{"system", "required": [{"name", "found_path", "md5", "status", "optional", "note"}]}]}
    Danh sách "required" gồm cả file trong catalog["bios_optional"] với "optional": true (vẫn tính
    status, nhưng thiếu thì KHÔNG phải lỗi: theo libretro các file đó là Optional). File bắt buộc: "optional": false.
    "expected_md5": danh sách md5 hợp lệ theo bios_hashes.json (rỗng nếu không có nguồn công khai) — có ở MỌI hàng,
    cả file đang có lẫn file thiếu.
    status: ok | wrong_hash | missing | unknown_hash
      ok           : md5 nằm trong danh sách md5 hợp lệ của bios_hashes.json
      wrong_hash   : có file nhưng md5 không nằm trong danh sách (hỏng / sai bản)
      missing      : không thấy trong BIOS/ của thẻ nào (kể cả thư mục con)
      unknown_hash : không có md5 từ nguồn công khai cho file này -> chỉ biết là tồn tại

Nhóm thay thế (catalog["bios_any_of"] = [[tên, tên, ...], ...], vd. PSX scph5500/5501/5502 theo vùng
game): các file trong nhóm thay thế nhau, chỉ cần ÍT NHẤT MỘT file ok (hoặc unknown_hash: có mặt nhưng
chưa có md5 công khai để đối chiếu). Mỗi file của nhóm vẫn có hàng trong "required" kèm "group": [tên...];
"optional" của hàng đó = True khi nhóm đã đạt (file còn lại KHÔNG bị coi là thiếu), False khi cả nhóm chưa
đạt. Thêm vào mỗi hệ: "any_of": [{"names", "satisfied", "severity": "ok"|"warning", "status": tốt nhất
trong nhóm, "note"}]. Cả nhóm thiếu/sai hash -> severity "warning".

bios_hashes.json: {"<file>": {"md5": [...], "source": "<url>", "systems": [...]}}.
Chỉ chứa md5 lấy từ docs.libretro.com; file nào không có hash trong nguồn thì không có mục.
Nếu bios_hashes.json không đọc được thì mọi file có mặt đều là unknown_hash (không bịa).
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BIOS_DIR = "BIOS"
MAX_HASH_BYTES = 64 * 1024 * 1024
NO_DOWNLOAD = "LoongMate không tải BIOS — bạn tự cung cấp bản hợp lệ."
_RANK = {"ok": 3, "wrong_hash": 2, "unknown_hash": 1, "misplaced": 0}
HOW_TO_ADD = ("Có BIOS bạn tự trích từ máy của mình: chép vào thư mục Import/ trên thẻ rồi Library > Nhập game — "
              "LoongMate nhận ra BIOS bằng md5, đổi đúng tên và đặt đúng thư mục.")


def find_hashes_file() -> Path:
    here = Path(__file__).resolve().parent
    for c in (here.parent / "bios_hashes.json", here.parent / "res" / "bios_hashes.json",
              here / "bios_hashes.json"):
        if c.is_file():
            return c
    return here.parent / "bios_hashes.json"


def load_hashes(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    p = Path(path) if path else find_hashes_file()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for name, ent in data.items():
        if isinstance(ent, dict) and isinstance(ent.get("md5"), list):
            out[name.lower()] = {**ent, "md5": [m.lower() for m in ent["md5"]]}
    return out


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _candidates(roots: List[Path], name: str) -> List[Path]:
    found = []
    for root in roots:
        bdir = root / BIOS_DIR
        try:
            if not bdir.is_dir():
                continue
            for p in bdir.rglob("*"):
                if p.name.lower() == name.lower() and p.is_file():
                    found.append(p)
        except OSError:
            continue
    return found


def _evaluate(path: Path, name: str, ent: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    res = {"name": name, "found_path": str(path), "md5": None}
    try:
        size = path.stat().st_size
        if size > MAX_HASH_BYTES:
            return {**res, "status": "unknown_hash",
                    "note": f"File quá lớn ({size} byte) để băm; chỉ xác nhận có tồn tại."}
        res["md5"] = _md5(path)
    except OSError as e:
        return {**res, "status": "unknown_hash", "note": f"Không đọc được file để băm: {e}"}
    if not ent or not ent["md5"]:
        return {**res, "status": "unknown_hash",
                "note": "Chưa có md5 từ nguồn công khai cho file này nên chỉ xác nhận có tồn tại."}
    if res["md5"] in ent["md5"]:
        return {**res, "status": "ok", "note": f"md5 khớp (nguồn: {ent.get('source')})."}
    return {**res, "status": "wrong_hash",
            "note": (f"{name} có md5 {res['md5']} nhưng mong đợi {' hoặc '.join(ent['md5'])}. "
                     f"File có thể hỏng hoặc là bản khác nên game có thể không chạy hoặc lỗi âm thanh/đồ hoạ. "
                     f"{NO_DOWNLOAD}")}


def check_bios(primary_path, secondary_path, catalog: Dict[str, Any],
               active_systems: Iterable[str], hashes_path=None) -> Dict[str, Any]:
    roots = [Path(p) for p in (primary_path, secondary_path) if p]
    hashes = load_hashes(hashes_path)
    systems, seen = [], set()
    for raw in sorted({s.upper() for s in active_systems}):
        info = catalog.get(raw)
        if not info:
            continue
        canon = (info.get("folder") or info.get("id") or raw).upper()
        if canon in seen:
            continue
        seen.add(canon)
        required = []
        any_of = []
        groups = [[n for n in g] for g in info.get("bios_any_of", []) if g]
        names = [(n, False) for n in info.get("bios", [])] + [(n, True) for n in info.get("bios_optional", [])]
        names += [(n, False) for g in groups for n in g]

        hle = info.get("bios_hle")
        sub = info.get("bios_dir")

        def eval_name(name, optional):
            ent = hashes.get(name.lower())
            cands = _candidates(roots, name)
            expected = list(ent["md5"]) if ent and ent.get("md5") else []
            if not cands:
                exp = f" (md5 mong đợi: {' hoặc '.join(ent['md5'])})" if ent and ent["md5"] else ""
                where = f" vào {BIOS_DIR}/{sub}/" if sub else ""
                if hle:
                    note = f"Chưa có {name}{exp} — không bắt buộc: {hle} Thêm{where} nếu một game không chạy. {HOW_TO_ADD}"
                elif optional:
                    note = f"Thiếu {name}{exp}; không bắt buộc — một số game của hệ {canon} cần để chạy đúng. {HOW_TO_ADD}"
                else:
                    note = f"Thiếu {name}{exp}{where}; hệ {canon} có thể không chạy được game. {HOW_TO_ADD}"
                return {"name": name, "found_path": None, "md5": None, "status": "missing",
                        "optional": optional or bool(hle), "expected_md5": expected, "note": note}
            if sub:
                inside = [c for c in cands if c.parent.name.lower() == sub.lower() and c.parent.parent.name == BIOS_DIR]
                if not inside:
                    return {"name": name, "found_path": str(cands[0]), "md5": None, "status": "misplaced",
                            "optional": optional or bool(hle), "expected_md5": expected,
                            "note": f"Có {name} ở {cands[0]} nhưng emulator chỉ đọc {BIOS_DIR}/{sub}/ — chuyển file vào đó."}
                cands = inside
            evals = [_evaluate(c, name, ent) for c in cands]
            best = dict(max(evals, key=lambda e: _RANK[e["status"]]))
            best["optional"] = optional
            best["expected_md5"] = expected
            return best

        rows = {}
        for name, optional in names:
            rows[name] = eval_name(name, optional)
        for g in groups:
            best = max((rows[n] for n in g), key=lambda r: _RANK.get(r["status"], 0))
            satisfied = best["status"] in ("ok", "unknown_hash")
            for n in g:
                rows[n]["group"] = list(g)
                if satisfied and rows[n]["status"] == "missing":
                    rows[n]["optional"] = True
                    rows[n]["note"] = (f"Không cần {n}: nhóm {', '.join(g)} thay thế nhau theo vùng game "
                                       f"và đã có {best['name']}.")
            if satisfied:
                note = f"Nhóm {', '.join(g)} đạt: có {best['name']} ({best['status']})."
            elif best["status"] == "wrong_hash":
                note = (f"Nhóm {', '.join(g)}: chỉ có file sai md5 ({best['name']}). Cần ít nhất một file đúng. "
                        f"{NO_DOWNLOAD}")
            elif hle:
                note = (f"Chưa có BIOS thật nào trong nhóm {', '.join(g)} — không bắt buộc: {hle} {HOW_TO_ADD}")
            else:
                note = (f"Thiếu cả nhóm {', '.join(g)}: cần ít nhất một file (thay thế nhau theo vùng game), "
                        f"hệ {canon} có thể không chạy được game. {HOW_TO_ADD}")
            any_of.append({"names": list(g), "satisfied": satisfied,
                           "severity": "ok" if satisfied else ("info" if hle else "warning"),
                           "status": best["status"], "note": note})
        required = [rows[n] for n, _ in names]
        if required:
            entry = {"system": canon, "required": required}
            if hle:
                entry["hle"] = hle
            if any_of:
                entry["any_of"] = any_of
            systems.append(entry)
    return {"systems": systems}
