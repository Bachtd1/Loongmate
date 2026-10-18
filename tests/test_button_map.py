"""Bảng nút của giao diện phải khớp bảng nút chính thức của Leaf (Catastrophe, mlp1).

Lỗi gốc 2026-10-02: mở GameController làm mỗi lần bấm đến hai lần với hai bảng
số khác nhau; người dùng thấy nút "tùm lum". Test này ghim bảng nút vào nguồn
upstream (native/third_party/catastrophe/include/catastrophe.h), không vào hằng
số tự đặt trong code của mình.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ROOT / "native" / "third_party" / "catastrophe" / "include" / "catastrophe.h"


def upstream_mlp1():
    txt = HEADER.read_text(encoding="utf-8", errors="replace")
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"#define\s+CAT__MLP1_BTN_(\w+)\s+(\d+)", txt)}


class TestButtonMap(unittest.TestCase):
    @unittest.skipUnless(HEADER.exists(), "chưa vendor Catastrophe")
    def test_face_and_system_buttons_match_leaf(self):
        from engine.ui_screens import BTN_MAP
        up = upstream_mlp1()
        expect = {up["A"]: "A", up["B"]: "B", up["X"]: "X", up["Y"]: "Y",
                  up["MENU"]: "B", up["START"]: "A"}
        for num, act in expect.items():
            self.assertEqual(BTN_MAP.get(num), act, f"nút {num} phải là {act}")

    def test_physical_a_selects_and_b_goes_back(self):
        """Pocket 1 theo bố cục Nintendo: nút A vật lý (1) chọn, B vật lý (0) quay lại."""
        from engine.ui_screens import BTN_MAP
        self.assertEqual(BTN_MAP[1], "A")
        self.assertEqual(BTN_MAP[0], "B")


if __name__ == "__main__":
    unittest.main()
