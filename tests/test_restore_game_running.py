"""Khôi phục / rollback save khi emulator đang chạy: emulator còn giữ save trong RAM và ghi đè khi thoát game,
nên phải từ chối và KHÔNG ghi gì (không tạo pre-restore, không journal)."""
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import savevault as SV  # noqa: E402


class TestRestoreWhileGameRunning(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_restore_busy_"))
        self.sd1, self.ud = self.tmp / "sd1", self.tmp / "ud"
        self.save = self.sd1 / "Saves" / "mGBA" / "Zelda.srm"
        self.save.parent.mkdir(parents=True)
        self.ud.mkdir()
        self.vault = SV.SaveVault(self.sd1, None, self.ud, max_retention=5)
        self.save.write_bytes(b"old")
        self.sid = self.vault.create_snapshot(label="manual")["snapshot_id"]
        time.sleep(0.01)
        self.save.write_bytes(b"new")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_refused_while_emulator_running(self):
        n_snaps = len(self.vault.list_snapshots())
        with mock.patch.object(SV.ProcWatcher, "scan", return_value={"1234": "retroarch"}):
            res = self.vault.restore_snapshot(self.sid)
        self.assertFalse(res["success"])
        self.assertEqual((res["error_kind"], res["running"]), ("game_running", ["retroarch"]))
        self.assertEqual(self.save.read_bytes(), b"new", "không được ghi khi game đang chạy")
        self.assertEqual(len(self.vault.list_snapshots()), n_snaps, "không được tạo pre-restore snapshot")
        self.assertFalse(self.vault.journal_file.exists())

    def test_restore_works_when_no_emulator(self):
        with mock.patch.object(SV.ProcWatcher, "scan", return_value={}):
            res = self.vault.restore_snapshot(self.sid)
        self.assertTrue(res["success"], res)
        self.assertEqual(self.save.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
