"""Trích mọi chuỗi tiếng Việt người dùng thấy, để dịch sang ngôn ngữ khác (i18n/en.json).

  python i18n/extract.py              -> i18n/strings.json  (danh sách khoá cần dịch, kèm nơi xuất hiện)
  python i18n/extract.py --missing    -> in các khoá chưa có trong i18n/en.json

Khoá là CHÍNH chuỗi tiếng Việt lúc chạy:
  * native/src/*.c: chuỗi C sau khi nối các literal liền nhau và bỏ escape; chuỗi định dạng giữ nguyên %s/%d/%.0f...
    (lm_ui.c tra khoá bằng chuỗi định dạng TRƯỚC khi điền giá trị).
  * engine/*.py: hằng chuỗi giữ nguyên; f-string thành mẫu với {} cho mỗi giá trị (engine/i18n.py so khớp mẫu).
Bỏ qua chú thích, docstring, và giao diện Python dự phòng (ui_screens/ui_sdl/tui: chỉ tiếng Việt)."""
import ast
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VI = re.compile("[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
PY_SKIP = ("ui_screens.py", "ui_sdl.py", "tui.py")
_C_LIT_RUN = re.compile(r'"(?:[^"\\\n]|\\.)*"(?:\s*"(?:[^"\\\n]|\\.)*")*')
_C_LIT = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_C_ESC = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "r": "\r", "0": "\0"}


def _c_unescape(s):
    return re.sub(r"\\(.)", lambda m: _C_ESC.get(m.group(1), m.group(1)), s)


def _strip_c_comments(t):
    out, i, n = [], 0, len(t)
    while i < n:
        if t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif t.startswith("//", i):
            j = t.find("\n", i)
            i = n if j < 0 else j
        elif t[i] == '"':
            m = re.compile(r'"(?:[^"\\\n]|\\.)*"').match(t, i)
            end = m.end() if m else i + 1
            out.append(t[i:end])
            i = end
        elif t[i] == "'":
            m = re.compile(r"'(?:[^'\\\n]|\\.)*'").match(t, i)
            end = m.end() if m else i + 1
            out.append(t[i:end])
            i = end
        else:
            out.append(t[i])
            i += 1
    return "".join(out)


def c_strings():
    found = {}
    for f in sorted((ROOT / "native" / "src").glob("*.c")):
        text = _strip_c_comments(f.read_text(encoding="utf-8"))
        for run in _C_LIT_RUN.finditer(text):
            s = _c_unescape("".join(_C_LIT.findall(run.group(0))))
            if VI.search(s):
                found.setdefault(s, set()).add(f.name)
    return found


def _fstring_template(node):
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value).replace("{", "{{").replace("}", "}}"))
        else:
            parts.append("{}")
    return "".join(parts)


def py_strings():
    found = {}
    for f in sorted((ROOT / "engine").glob("*.py")):
        if f.name.endswith(PY_SKIP):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docs, in_fstr = set(), set()
        for n in ast.walk(tree):
            body = getattr(n, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
            if isinstance(n, ast.JoinedStr):
                in_fstr.update(id(v) for v in n.values)
        for n in ast.walk(tree):
            if isinstance(n, ast.JoinedStr):
                s = _fstring_template(n)
                if VI.search(s):
                    found.setdefault(s, set()).add(f.name)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs and id(n) not in in_fstr:
                if VI.search(n.value):
                    found.setdefault(n.value.replace("{", "{{").replace("}", "}}"), set()).add(f.name)
    return found


def collect():
    out = {}
    for k, v in c_strings().items():
        out.setdefault(k, {"kind": set(), "files": set()})
        out[k]["kind"].add("c")
        out[k]["files"] |= v
    for k, v in py_strings().items():
        out.setdefault(k, {"kind": set(), "files": set()})
        out[k]["kind"].add("py")
        out[k]["files"] |= v
    return {k: {"kind": sorted(v["kind"]), "files": sorted(v["files"])} for k, v in sorted(out.items())}


if __name__ == "__main__":
    data = collect()
    if "--missing" in sys.argv:
        en = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8")) if (ROOT / "i18n" / "en.json").exists() else {}
        miss = [k for k in data if k not in en]
        sys.stdout.buffer.write(("\n".join(json.dumps(k, ensure_ascii=False) for k in miss) + "\n").encode("utf-8"))
        print(f"{len(miss)}/{len(data)} chưa dịch", file=sys.stderr)
    else:
        io.open(ROOT / "i18n" / "strings.json", "w", encoding="utf-8", newline="\n").write(
            json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        print(f"{len(data)} khoá -> i18n/strings.json")
