"""Giao diện tiếng Anh: engine dịch ở lối ra JSON; danh mục i18n/en.json phải giữ đúng chỗ trống (%s, {}) của bản gốc."""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import i18n  # noqa: E402

CAT = {
    "Đã ghép ảnh bìa: {}": "Matched cover art: {}",
    "Thẻ {} sắp hết dung lượng ({}MB)": "Card {} is almost full ({}MB)",
    "không đọc được": "unreadable",
    "Sao lưu tự động": "Automatic backup",
    "Leaf đã đổi cấu trúc: ": "Leaf changed its structure: ",
    "không có socket {} (jawakad)": "no socket {} (jawakad)",
    "Giữ {{x}} nguyên": "Keep {{x}} as is",
    "Sai số chỗ trống {}": "Wrong placeholders {} {}",
}


class Translate(unittest.TestCase):
    def setUp(self):
        self.tr = i18n.Translator(CAT)

    def test_exact_and_template(self):
        self.assertEqual(self.tr.text("không đọc được"), "unreadable")
        self.assertEqual(self.tr.text("Đã ghép ảnh bìa: Zelda (USA).png"), "Matched cover art: Zelda (USA).png")
        self.assertEqual(self.tr.text("Thẻ SD2 sắp hết dung lượng (120MB)"), "Card SD2 is almost full (120MB)")

    def test_nested_value_is_translated_too(self):
        self.assertEqual(self.tr.text("Đã ghép ảnh bìa: không đọc được"), "Matched cover art: unreadable")

    def test_concatenated_message_translates_known_fragments(self):
        s = "Leaf đã đổi cấu trúc: " + "không có socket /tmp/x.sock (jawakad)"
        self.assertIn("Leaf changed its structure: ", self.tr.text(s))

    def test_unknown_text_and_ascii_are_kept(self):
        self.assertEqual(self.tr.text("Câu chưa có trong danh mục"), "Câu chưa có trong danh mục")
        self.assertEqual(self.tr.text("snap_20261018_auto"), "snap_20261018_auto")

    def test_braces_escaped(self):
        self.assertEqual(self.tr.text("Giữ {x} nguyên"), "Keep {x} as is")

    def test_wrong_placeholder_count_is_ignored(self):
        self.assertEqual(self.tr.text("Sai số chỗ trống 3"), "Sai số chỗ trống 3")

    def test_paths_and_names_are_never_translated(self):
        o = {"message": "không đọc được", "path": "/mnt/sdcard/không đọc được.gba", "items": ["không đọc được"],
             "issues": [{"title": "Sao lưu tự động", "rom": "Sao lưu tự động.gba"}]}
        out = self.tr.obj(o)
        self.assertEqual(out["message"], "unreadable")
        self.assertEqual(out["path"], o["path"])
        self.assertEqual(out["items"], ["không đọc được"])
        self.assertEqual(out["issues"][0], {"title": "Automatic backup", "rom": "Sao lưu tự động.gba"})


class Env(unittest.TestCase):
    def test_vietnamese_is_default_and_untouched(self):
        with mock.patch.dict("os.environ", {"LOONGMATE_LANG": ""}):
            self.assertEqual(i18n.translate_text("không đọc được"), "không đọc được")

    def test_cli_json_output_is_translated(self):
        import contextlib
        import io
        import tempfile
        import shutil
        import engine.cli as CLI
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "ud").mkdir()
        i18n._cache.clear()
        self.addCleanup(i18n._cache.clear)
        with mock.patch.dict("os.environ", {"LOONGMATE_LANG": "en"}), \
                mock.patch.object(i18n, "_catalog_paths", return_value=[tmp / "en.json"]):
            (tmp / "en.json").write_text(json.dumps({"BẬT khoá khi thức dậy (qua LoongMate Agent; Agent phải đang chạy).":
                                                     "Lock on wake ON (via LoongMate Agent; the Agent must be running)."}),
                                         encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                CLI.main(["--primary", str(tmp), "--userdata", str(tmp / "ud"), "--json", "pocketlock", "on"])
        self.assertIn("Lock on wake ON", json.loads(out.getvalue())["message"])


class Catalog(unittest.TestCase):
    """Danh mục thật: mỗi bản dịch giữ đúng chỗ trống của bản gốc, nếu không giao diện in sai hoặc sập (printf)."""
    SPEC = re.compile(r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|z|j|t)?[diouxXeEfgGcspn%]")

    def test_placeholders_match(self):
        p = ROOT / "i18n" / "en.json"
        if not p.exists():
            self.skipTest("chưa có i18n/en.json")
        cat = json.loads(p.read_text(encoding="utf-8"))
        bad = []
        for vi, en in cat.items():
            if not en:
                continue
            if self.SPEC.findall(vi) != self.SPEC.findall(en):
                bad.append(vi)
            if vi.replace("{{", "").replace("}}", "").count("{}") != en.replace("{{", "").replace("}}", "").count("{}"):
                bad.append(vi)
        self.assertEqual(bad[:20], [], f"{len(bad)} bản dịch sai chỗ trống")


class NativeWrapped(unittest.TestCase):
    def test_every_vietnamese_string_in_native_ui_goes_through_tr(self):
        """Chuỗi tiếng Việt mới trong thân hàm C phải bọc tr(...), nếu không giao diện tiếng Anh sẽ lẫn tiếng Việt.
        Sửa: python native/tools/wrap_tr.py (bọc tự động), rồi thêm bản dịch vào i18n/en.json."""
        import subprocess
        import os
        r = subprocess.run([sys.executable, str(ROOT / "native" / "tools" / "wrap_tr.py"), "--check"],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main()
