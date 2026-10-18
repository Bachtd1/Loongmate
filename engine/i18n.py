"""Dịch chuỗi engine trả cho giao diện khi LOONGMATE_LANG=en (giao diện native đặt theo Máy > Ngôn ngữ).

Mã engine viết tiếng Việt như cũ; ta dịch ở lối ra (cli_impl._print_json) theo danh mục i18n/en.json, khoá là chuỗi
tiếng Việt (xem i18n/extract.py). Thứ tự thử:
  1. khớp nguyên chuỗi;
  2. khớp mẫu f-string ("Đã ghép ảnh bìa: {}" -> "Matched cover art: {}"), giá trị giữ nguyên;
  3. chuỗi ghép từ nhiều mảnh ("a" + x + "b", "; ".join(...)): thay từng mảnh dài đã biết, dài trước.
Không dịch được thì giữ tiếng Việt (không bao giờ làm hỏng dữ liệu). Trường chứa đường dẫn/tên file/tên game không dịch."""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SKIP_KEYS = {"path", "paths", "rom", "rom_path", "roms", "file", "files", "src", "dest", "source", "target", "target_path",
              "file_path", "folder", "name", "names", "stem", "items", "discs", "id", "ids", "url", "urls", "href", "title_raw",
              "snapshot_id", "launch_id", "sha256", "md5", "core", "system", "card", "label_raw", "query", "output"}
_MIN_FRAGMENT = 6


def lang() -> str:
    return (os.environ.get("LOONGMATE_LANG") or "vi").strip().lower()


def _catalog_paths(name: str) -> List[Path]:
    here = Path(__file__).resolve().parent
    return [here.parent / "res" / f"i18n_{name}.json",       # trong pak: <pak>/res/i18n_en.json
            here.parent / "i18n" / f"{name}.json"]           # repo: i18n/en.json


class Translator:
    def __init__(self, catalog: Dict[str, str]):
        self.exact: Dict[str, str] = {}
        self.templates: List[Tuple[re.Pattern, str, int]] = []
        frags = []
        for vi, en in catalog.items():
            if not isinstance(en, str) or not en:
                continue
            if "{}" in vi.replace("{{", "").replace("}}", ""):
                lit = re.split(r"(?<!\{)\{\}(?!\})", vi)
                if en.count("{}") != len(lit) - 1:
                    continue                                   # bản dịch sai số chỗ trống: bỏ, giữ tiếng Việt
                pat = "^" + "(.+?)".join(re.escape(p.replace("{{", "{").replace("}}", "}")) for p in lit) + "$"
                self.templates.append((re.compile(pat, re.S), en, sum(len(p) for p in lit)))
            else:
                vi_rt, en_rt = vi.replace("{{", "{").replace("}}", "}"), en.replace("{{", "{").replace("}}", "}")
                self.exact[vi_rt] = en_rt
                if len(vi_rt) >= _MIN_FRAGMENT:
                    frags.append((vi_rt, en_rt))
        self.templates.sort(key=lambda t: -t[2])               # mẫu nhiều chữ cố định hơn thì cụ thể hơn
        frags.sort(key=lambda t: -len(t[0]))
        self.frags = frags

    def _template(self, s: str) -> Optional[str]:
        for pat, en, _ in self.templates:
            m = pat.match(s)
            if m:
                vals = [self.text(v) for v in m.groups()]     # giá trị có thể lại là câu tiếng Việt (lồng)
                out, i = [], 0
                for part in re.split(r"(\{\})", en.replace("{{", "\0L").replace("}}", "\0R")):
                    if part == "{}":
                        out.append(vals[i] if i < len(vals) else "")
                        i += 1
                    else:
                        out.append(part.replace("\0L", "{").replace("\0R", "}"))
                return "".join(out)
        return None

    def _fragments(self, s: str) -> str:
        for vi, en in self.frags:
            if vi in s:
                s = s.replace(vi, en)
        return s

    def text(self, s: str) -> str:
        if not s or s.isascii():
            return s
        if s in self.exact:
            return self.exact[s]
        t = self._template(s)
        if t is not None:
            return t
        if "\n" in s:
            return "\n".join(self.text(line) for line in s.split("\n"))
        return self._fragments(s)

    def obj(self, o: Any, key: str = "") -> Any:
        if isinstance(o, str):
            return o if key in _SKIP_KEYS else self.text(o)
        if isinstance(o, list):
            return [self.obj(v, key) for v in o]
        if isinstance(o, dict):
            return {k: self.obj(v, k) for k, v in o.items()}
        return o


_cache: Dict[str, Optional[Translator]] = {}


def translator(name: Optional[str] = None) -> Optional[Translator]:
    name = name or lang()
    if name in ("vi", ""):
        return None
    if name not in _cache:
        tr = None
        for p in _catalog_paths(name):
            try:
                tr = Translator(json.loads(p.read_text(encoding="utf-8")))
                break
            except (OSError, ValueError):
                continue
        _cache[name] = tr
    return _cache[name]


def translate_obj(o: Any) -> Any:
    tr = translator()
    return tr.obj(o) if tr else o


def translate_text(s: str) -> str:
    tr = translator()
    return tr.text(s) if tr else s
