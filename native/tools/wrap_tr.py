"""Bọc mọi chuỗi tiếng Việt trong THÂN HÀM của native/src/*.c bằng tr(...) (lm_i18n.h) để giao diện dịch được.

    python native/tools/wrap_tr.py            # sửa tại chỗ, in số chuỗi đã bọc mỗi file
    python native/tools/wrap_tr.py --check    # chỉ liệt kê chuỗi tiếng Việt chưa bọc (0 = sạch)

Không đụng: chú thích, dòng tiền xử lý (#define/#include), khởi tạo ở mức file (mảng tĩnh: C không cho gọi hàm ở đó,
nơi dùng phải tự tr()), chuỗi đã nằm trong tr(...), dòng có chú thích /* no-tr */. Các literal liền nhau ("a" "b") được bọc chung một lần."""
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
VI = re.compile("[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
SKIP_FILES = {"lm_i18n.c", "lm_hello.c"}      # lm_hello: binary thử nghiệm tối giản, không liên kết lm_i18n
_LIT = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def process(text):
    """Trả (văn bản mới, số đã bọc, danh sách chưa bọc được ở mức file)."""
    out, i, n = [], 0, len(text)
    depth = 0
    init_depth = None            # độ sâu của khởi tạo mức file "= {" (không bọc bên trong)
    line_start = True
    wrapped, skipped = 0, []
    while i < n:
        ch = text[i]
        if line_start and ch == "#":                          # dòng tiền xử lý (kể cả nối dòng bằng \)
            j = i
            while True:
                e = text.find("\n", j)
                if e < 0:
                    e = n
                    break
                if text[e - 1] == "\\":
                    j = e + 1
                    continue
                break
            seg = text[i:e]
            for m in _LIT.finditer(seg):
                if VI.search(m.group(0)):
                    skipped.append(m.group(0)[:60])
            out.append(seg)
            i = e
            continue
        if text.startswith("/*", i):
            e = text.find("*/", i + 2)
            e = n if e < 0 else e + 2
            out.append(text[i:e]); i = e; continue
        if text.startswith("//", i):
            e = text.find("\n", i)
            e = n if e < 0 else e
            out.append(text[i:e]); i = e; continue
        if ch == "'":
            m = re.compile(r"'(?:[^'\\\n]|\\.)*'").match(text, i)
            e = m.end() if m else i + 1
            out.append(text[i:e]); i = e; line_start = False; continue
        if ch == '"':
            # gom cả dãy literal liền nhau
            j, parts = i, []
            while True:
                m = _LIT.match(text, j)
                if not m:
                    break
                parts.append((j, m.end()))
                k = m.end()
                while k < n and text[k] in " \t\r\n":
                    k += 1
                if k < n and text[k] == '"':
                    j = k
                    continue
                break
            e = parts[-1][1] if parts else i + 1
            run = text[i:e]
            prev = "".join(out).rstrip()
            already = prev.endswith("tr(")
            title = prev.endswith("ui_screen_new(")              # tiêu đề màn: lm_ui.c dịch lúc vẽ
            eol = text.find("\n", i)
            no_tr = "/* no-tr */" in text[i:eol if eol >= 0 else n]      # cố ý không dịch (nhãn song ngữ...)
            in_body = depth >= 1 and init_depth is None and not no_tr
            if VI.search(run) and not already and not title:
                if in_body:
                    out.append("tr(" + run + ")")
                    wrapped += 1
                else:
                    skipped.append(run[:60])
                    out.append(run)
            else:
                out.append(run)
            i = e
            line_start = False
            continue
        if ch == "{":
            if init_depth is None:
                prev = "".join(out).rstrip()
                if prev.endswith("="):
                    stmt = prev[max(prev.rfind(";"), prev.rfind("{"), prev.rfind("}")) + 1:]
                    if depth == 0 or re.search(r"\bstatic\b", stmt):
                        init_depth = depth                    # khởi tạo mức file / static trong hàm: = { ... };
            depth += 1
        elif ch == "}":
            depth -= 1
            if init_depth is not None and depth == init_depth:
                init_depth = None
        out.append(ch)
        line_start = ch == "\n" or (line_start and ch in " \t")
        i += 1
    return "".join(out), wrapped, skipped


def main():
    check = "--check" in sys.argv
    total_left = 0
    for f in sorted(SRC.glob("*.c")):
        if f.name in SKIP_FILES:
            continue
        text = f.read_text(encoding="utf-8")
        new, w, skipped = process(text)
        if check:
            if w:
                print(f"{f.name}: {w} chuỗi tiếng Việt chưa bọc tr()")
                total_left += w
            continue
        if w:
            if '#include "lm_i18n.h"' not in new:
                new = new.replace('#include "lm_app.h"', '#include "lm_app.h"\n#include "lm_i18n.h"', 1) \
                    if '#include "lm_app.h"' in new else new.replace('#include "lm_ui.h"', '#include "lm_ui.h"\n#include "lm_i18n.h"', 1)
            f.write_bytes(new.encode("utf-8"))
        print(f"{f.name}: bọc {w}, giữ nguyên ở mức file/#define {len(skipped)}")
    if check:
        print(f"tổng chưa bọc: {total_left}")
        sys.exit(1 if total_left else 0)


if __name__ == "__main__":
    main()
