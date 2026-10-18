"""Rà nhãn bị cắt "..." trong giao diện native: trích mọi chuỗi viết cứng ở ui_add(label, tag)/ui_head(...) trong
native/src/*.c, đo bằng chính lm-home (LM_MEASURE=1, cùng font và bố cục với lúc vẽ thật) và in những nhãn không vừa.

    python native/tools/label_audit.py --emit > labels.tsv
    # trên máy (font thật của Leaf; emulator dùng DejaVu nên đo sai):
    . <thẻ>/.system/leaf/platforms/mlp1/launcher/env.sh; SDL_VIDEODRIVER=offscreen LM_MEASURE=1 lm-home < labels.tsv
Nhãn có phần sinh động (%s, jstr(...)) bị bỏ qua: đó là dữ liệu (tên game, tên thẻ), cắt là đúng."""
import itertools
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _args(text, start):
    """Tách đối số của lời gọi bắt đầu ở text[start] == '('; trả (danh sách đối số, vị trí sau ')')."""
    depth, i, cur, out, in_str = 0, start, [], [], False
    while i < len(text):
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == "\\":
                cur.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            if depth > 1:
                cur.append(ch)
        elif ch == ")":
            depth -= 1
            if depth == 0:
                out.append("".join(cur).strip())
                return out, i + 1
            cur.append(ch)
        elif ch == "," and depth == 1:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    return out, i


def _unescape(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _choices(arg):
    """Các giá trị có thể của một đối số chuỗi; None nếu có phần sinh động."""
    if arg in ("NULL", '""', "0"):
        return [""]
    lits = _STR.findall(arg)
    if not lits:
        return None
    rest = _STR.sub("", arg).replace("tr()", "")       # tr("...") là chuỗi viết cứng đã bọc để dịch
    if "?" not in rest and re.search(r"[A-Za-z_]", rest):
        return None                                     # biến, jstr(...), hàm sinh chuỗi
    if "?" not in rest and len(lits) > 1:
        lits = ["".join(lits)]                          # "abc" "def" nối chuỗi
    vals = [_unescape(s) for s in lits]
    return None if any("%" in v for v in vals) else vals


def emit(lang=None):
    """lang="en": đo bản dịch (i18n/en.json) thay vì tiếng Việt; nhãn chưa dịch được đo bằng tiếng Việt."""
    cat = {}
    if lang:
        import json
        cat = json.loads((SRC.parents[1] / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
    t = lambda s: cat.get(s, s)  # noqa: E731
    rows = set()
    for f in sorted(SRC.glob("*.c")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\bui_(add|head)\s*\(", text):
            args, _ = _args(text, m.end() - 1)
            if m.group(1) == "add" and len(args) >= 3:
                labs, tags = _choices(args[1]), _choices(args[2])
                if labs is None:
                    continue
                for lab, tag in itertools.product(labs, tags if tags is not None else [""]):
                    if lab:
                        rows.add(f"{t(lab)}\t{t(tag) if tag else tag}")
            elif m.group(1) == "head" and len(args) == 2:
                for h in _choices(args[1]) or []:
                    rows.add(f"#HEAD\t{t(h)}")
    sys.stdout.buffer.write(("\n".join(sorted(rows)) + "\n").encode("utf-8"))


if __name__ == "__main__":
    if "--emit" in sys.argv:
        emit(sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else None)
    else:
        print(__doc__)
