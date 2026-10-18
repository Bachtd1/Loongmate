"""Gộp các phần dịch (JSON {tiếng Việt: English}) vào i18n/en.json, kiểm từng bản dịch trước khi nhận.

    python i18n/merge.py phan1.json phan2.json ...   [--replace]   (--replace: ghi đè bản dịch đã có)

Từ chối (giữ tiếng Việt) và in ra những bản dịch:
  * khác dãy chỉ thị printf (%s, %.0f...) hoặc số chỗ trống {} so với bản gốc;
  * khác khoảng trắng đầu/cuối hoặc tiền tố "! ", "!! ", "+ ";
  * thiếu "từ cố định" có trong bản gốc (tên riêng, số >= 2 chữ số, thư mục chuẩn, đuôi file): dấu hiệu bản dịch bị ghép lệch dòng.
Bỏ các khoá một ký tự (phím bàn phím ảo, không phải câu chữ)."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = re.compile(r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|z|j|t)?[diouxXeEfgGcspn%]")
KEEP = re.compile(r"(?:\b(?:Roms|Saves|States|Images|BIOS|Import|Quarantine)/|\.(?:json|cue|bin|m3u|pak|zip|7z|sh|py|log|png|jpg|cfg|srm|sav|state)\b|"
                  r"\b\d{2,}\b|\b(?:Leaf|LoongMate|SaveVault|RetroArch|BIOS|SD1|SD2|FAT32|exFAT|Jawaka|jawakad|PocketBridge|"
                  r"Tailscale|SHA-256|Hot Tabs|PortMaster|Select|Start|L1|R1)\b)")
IDENTITY = {"Ngôn ngữ / Language"}
PREFIXES = ("!! ", "! ", "+ ", "- ")


def placeholders(s):
    return SPEC.findall(s), s.replace("{{", "").replace("}}", "").count("{}")


def problems(vi, en):
    out = []
    if placeholders(vi) != placeholders(en):
        out.append("chỗ trống")
    if (vi[:len(vi) - len(vi.lstrip())] != en[:len(en) - len(en.lstrip())]
            or vi[len(vi.rstrip()):] != en[len(en.rstrip()):]):
        out.append("khoảng trắng")
    for p in PREFIXES:
        if vi.startswith(p) != en.startswith(p):
            out.append("tiền tố")
            break
    miss = [t for t in set(KEEP.findall(vi)) if t not in en]
    if miss:
        out.append("thiếu " + ", ".join(sorted(miss))[:60])
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    replace = "--replace" in sys.argv
    keys = json.loads((ROOT / "i18n" / "strings.json").read_text(encoding="utf-8"))
    out_path = ROOT / "i18n" / "en.json"
    cat = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    rejected, added = [], 0
    for a in args:
        for vi, en in json.loads(Path(a).read_text(encoding="utf-8")).items():
            if len(vi.strip()) <= 1 or not isinstance(en, str):
                continue
            if vi in IDENTITY:
                cat[vi] = vi
                continue
            prob = problems(vi, en)
            if prob:
                rejected.append((vi, en, prob))
                continue
            if vi not in cat or replace:
                cat[vi] = en
                added += 1
    out = {k: cat[k] for k in sorted(cat)}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    missing = [k for k in keys if k not in out and len(k.strip()) > 1]
    print(f"nhận {added}, từ chối {len(rejected)}, danh mục {len(out)}, còn thiếu {len(missing)}/{len(keys)}")
    rep = ROOT / "i18n" / "merge-report.json"
    rep.write_text(json.dumps({"rejected": [{"vi": v, "en": e, "why": p} for v, e, p in rejected], "missing": missing},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"chi tiết: {rep}")


if __name__ == "__main__":
    main()
