"""Nhãn dài cắt GIỮA để các bản chỉ khác ở đuôi (Blue/Red, Disc 1/2) vẫn phân biệt được trong màn Xoá ROM."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.ui_screens import fit_middle  # noqa: E402


def w(t):
    return 10 * len(t)


class TestFitMiddle(unittest.TestCase):
    def test_short_label_unchanged(self):
        self.assertEqual(fit_middle("Zelda.gba", 300, w), "Zelda.gba")

    def test_variants_differing_only_at_end_stay_distinct(self):
        # Phạm vi: khác nhau ở ĐUÔI (đĩa, vùng, Rev). Khác ở giữa (vd "Blue Advance"/"Red Advance") thì không cách cắt
        # nào giữ được ở độ rộng này — khung giải thích bên phải luôn hiện tên đầy đủ của game đang chọn.
        names = ["[ ] Pokemon Mystery Dungeon - Red Rescue Team (USA).gba",
                 "[ ] Pokemon Mystery Dungeon - Red Rescue Team (Europe).gba",
                 "[ ] Castlevania - Aria of Sorrow (USA) (Rev 1).gba",
                 "[ ] Castlevania - Aria of Sorrow (USA) (Rev 2).gba",
                 "[ ] Final Fantasy VII (USA) (Disc 1).cue", "[ ] Final Fantasy VII (USA) (Disc 2).cue"]
        out = [fit_middle(n, 300, w) for n in names]
        self.assertEqual(len(set(out)), len(out), out)
        for o in out:
            self.assertLessEqual(w(o), 300, o)
            self.assertIn("…", o)
        self.assertTrue(out[4].endswith("(Disc 1).cue"), out[4])

    def test_vietnamese_kept_whole(self):
        o = fit_middle("[ ] Chuyện Tình Đường Hầm Bí Mật Phần Hai (Việt Hoá).gba", 300, w)
        self.assertTrue(o.endswith("(Việt Hoá).gba"), o)
        self.assertLessEqual(w(o), 300)

    def test_narrow_column_still_keeps_short_tail(self):
        """Cột hẹp (14 ký tự đuôi không vừa): vẫn giữ đuôi ngắn hơn thay vì cắt cuối làm các bản giống nhau."""
        out = [fit_middle(f"[ ] Final Fantasy VII (USA) (Disc {i}).cue", 160, w) for i in (1, 2)]
        self.assertNotEqual(out[0], out[1], out)
        self.assertTrue(all(w(o) <= 160 for o in out), out)

    def test_too_narrow_falls_back_to_end_cut(self):
        o = fit_middle("Final Fantasy VII (USA) (Disc 1).cue", 60, w)
        self.assertLessEqual(w(o), 60)


if __name__ == "__main__":
    unittest.main()
