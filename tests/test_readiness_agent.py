"""Home báo khi LoongMate Agent không chạy: Agent tắt thì tự sao lưu, tự tiếp tục và khoá khi thức dậy đều ngừng mà
không ai hay. Chỉ báo khi thẻ có cài LoongMateAgent.pak (không báo nhầm khi chạy trên PC)."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine.cli as CLI  # noqa: E402
from engine import autobackup as ab_mod  # noqa: E402


class ReadinessAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_ready_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ud = self.tmp / "ud"
        self.ud.mkdir()

    def readiness(self, running):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(ab_mod, "pid_lock_held", return_value=running):
            CLI.main(["--primary", str(self.tmp), "--userdata", str(self.ud), "--json", "readiness"])
        return json.loads(out.getvalue())

    def install_agent(self):
        (self.tmp / "Apps" / "mlp1" / "LoongMateAgent.pak").mkdir(parents=True)

    def test_warns_when_agent_installed_but_not_running(self):
        self.install_agent()
        r = self.readiness(False)
        self.assertFalse(r["agent"]["running"])
        self.assertIn("Agent chưa chạy", r["device"]["warnings"][0])
        self.assertNotEqual(r["device"]["status"], "green")

    def test_quiet_when_running(self):
        self.install_agent()
        r = self.readiness(True)
        self.assertTrue(r["agent"]["running"])
        self.assertFalse(any("Agent" in w for w in r["device"]["warnings"]))

    def test_quiet_when_agent_not_installed(self):
        r = self.readiness(False)
        self.assertFalse(any("Agent" in w for w in r["device"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
