"""Khôi phục snapshot cũ nhất khi đã đủ hạn mức giữ: tạo pre-restore KHÔNG được prune chính snapshot
đang khôi phục (lỗi cũ: snapshot nguồn bị xoá + object bị GC giữa chừng, restore ghi 0 file mà vẫn báo
success). Và object thiếu thì restore phải báo thất bại, không lặng lẽ bỏ qua."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.savevault import SaveVault  # noqa: E402


class TestRestoreRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_restore_ret_"))
        self.sd1 = self.tmp / "sd1"
        self.ud = self.tmp / "ud"
        self.save = self.sd1 / "Saves" / "mGBA" / "Zelda.srm"
        self.save.parent.mkdir(parents=True)
        self.ud.mkdir()
        self.vault = SaveVault(self.sd1, None, self.ud, max_retention=5)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, content: bytes, t: int) -> str:
        self.save.write_bytes(content)
        os.utime(self.save, (t, t))
        res = self.vault.create_snapshot(label="manual")
        self.assertTrue(res["success"], res)
        return res["snapshot_id"]

    def five_snapshots(self):
        t = int(time.time()) - 1000
        return [self.snap(f"zelda-{i}".encode(), t + i * 10) for i in range(5)]

    def test_whole_snapshot_restore_of_oldest_when_retention_full(self):
        ids = self.five_snapshots()
        res = self.vault.restore_snapshot(ids[0])
        self.assertTrue(res["success"], res)
        self.assertEqual(self.save.read_bytes(), b"zelda-0", "phải ghi thật bản cũ nhất")
        self.assertEqual(res["restored_files_count"], 1)

    def test_missing_object_is_a_failure_not_silent_success(self):
        ids = self.five_snapshots()
        for p in (self.ud / "SaveVault" / "objects").rglob("*"):
            if p.is_file():
                p.unlink()
        self.save.write_bytes(b"current-different")   # pre-restore không được tạo lại đúng object đã mất
        res = self.vault.restore_snapshot(ids[-1])
        self.assertFalse(res["success"], res)
        self.assertIn("Saves/mGBA/Zelda.srm", res.get("missing_files", []))


    def test_cli_saves_restore_reports_missing_files(self):
        """Native đọc missing_files từ JSON của `saves restore` — không chỉ trong message."""
        import io, json
        from contextlib import redirect_stdout
        from engine import cli
        ids = self.five_snapshots()
        for p in (self.ud / "SaveVault" / "objects").rglob("*"):
            if p.is_file():
                p.unlink()
        self.save.write_bytes(b"current-different")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["--primary", str(self.sd1), "--userdata", str(self.ud), "--json", "--no-rescan",
                             "saves", "restore", "zelda", "--snapshot", ids[-1], "--yes"])
        out = json.loads(buf.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "RESTORE_FAILED")
        self.assertEqual(out["missing_files"], ["Saves/mGBA/Zelda.srm"])
        self.assertTrue(out["pre_restore_id"])


if __name__ == "__main__":
    unittest.main()
