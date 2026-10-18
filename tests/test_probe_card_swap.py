"""Hai thẻ đổi chỗ (đã thấy trên máy thật 2026-10-04): ngoài launcher, UMRK_SECONDARY_SDCARD_PATH
mặc định trỏ CHÍNH thẻ Leaf -> probe phải tìm thẻ còn lại đang mount, không coi thẻ Leaf là thẻ phụ."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import probe as P  # noqa: E402


class TestCardSwap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leaf = Path(self.tmp.name) / "media_sdcard1"
        self.other = Path(self.tmp.name) / "mnt_sdcard"
        for d in (self.leaf, self.other):
            d.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, env, mounted):
        mounts = {str(p.resolve()) for p in mounted}
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(P, "CARD_MOUNT_CANDIDATES", (str(self.other), str(self.leaf))), \
                mock.patch("os.path.ismount", lambda p: str(Path(p).resolve()) in mounts):
            for k in ("SDCARD_PATHS", "SECONDARY_SDCARD_PATH"):
                os.environ.pop(k, None)
            return P.PlatformProbe()

    def test_secondary_equal_to_primary_finds_other_card(self):
        pr = self.probe({"SDCARD_PATH": str(self.leaf), "UMRK_SECONDARY_SDCARD_PATH": str(self.leaf)},
                        [self.leaf, self.other])
        self.assertEqual(pr.primary_path, self.leaf.resolve())
        self.assertEqual(pr.secondary_path, self.other.resolve())

    def test_no_other_card_means_no_secondary(self):
        pr = self.probe({"SDCARD_PATH": str(self.leaf), "UMRK_SECONDARY_SDCARD_PATH": str(self.leaf)},
                        [self.leaf])
        self.assertIsNone(pr.secondary_path)

    def test_normal_case_unchanged(self):
        pr = self.probe({"SDCARD_PATH": str(self.other), "UMRK_SECONDARY_SDCARD_PATH": str(self.leaf)},
                        [self.leaf, self.other])
        self.assertEqual(pr.secondary_path, self.leaf.resolve())


if __name__ == "__main__":
    unittest.main()
