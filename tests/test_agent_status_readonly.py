"""agent.py status chỉ đọc: không tạo/ghi agent.lock (lỗi cũ: dùng PidLock.acquire)."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import agent  # noqa: E402


class TestAgentStatusReadOnly(unittest.TestCase):
    def run_status(self, d):
        with redirect_stdout(io.StringIO()) as out:
            agent.cmd_status(Path(d), Path(d) / "khong-co.log")
        return out.getvalue()

    def test_no_lock_file_created(self):
        with tempfile.TemporaryDirectory() as d:
            out = self.run_status(d)
            self.assertIn("KHONG chay", out)
            self.assertFalse((Path(d) / agent.LOCK_NAME).exists())

    def test_existing_lock_content_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            lk = Path(d) / agent.LOCK_NAME
            lk.write_bytes(b"4242")
            self.run_status(d)
            self.assertEqual(lk.read_bytes(), b"4242")

    @unittest.skipIf(os.name == "nt", "flock chỉ có trên Linux")
    def test_held_lock_detected(self):
        with tempfile.TemporaryDirectory() as d:
            lock = agent.PidLock(Path(d) / agent.LOCK_NAME)
            self.assertTrue(lock.acquire())
            try:
                self.assertIn("dang chay", self.run_status(d))
            finally:
                lock.release()


if __name__ == "__main__":
    unittest.main()
