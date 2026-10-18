"""Màn Bắt đầu (lần đầu mở app): mỗi bước có trạng thái thật để người mới không phải tự tìm Agent, tự tiếp tục, khoá,
sao lưu lần đầu, quét thư viện trong menu. `onboarding done` để không hiện lại."""
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
from engine import agent as agent_mod  # noqa: E402
from engine import autoresume as AR  # noqa: E402
from engine import pocketlock as PL  # noqa: E402


class Onboarding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_onb_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ud = self.tmp / "ud"
        self.ud.mkdir()

    def run_cli(self, *argv, running=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(agent_mod, "lock_held", return_value=running):
            code = CLI.main(["--primary", str(self.tmp), "--userdata", str(self.ud), "--json", "onboarding", *argv])
        return code, json.loads(out.getvalue())

    def test_fresh_install_shows_every_step_pending(self):
        code, st = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertFalse(st["done"])
        self.assertFalse(st["agent_running"])
        self.assertFalse(st["autoresume"])
        self.assertFalse(st["pocketlock"])
        self.assertEqual(st["snapshots"], 0)
        self.assertFalse(st["library_scanned"])

    def test_reflects_real_state(self):
        AR.set_enabled(self.ud, True)
        PL.set_enabled(self.ud, True)
        (self.ud / "cache").mkdir()
        (self.ud / "cache" / "last_audit.json").write_text("{}", encoding="utf-8")
        _, st = self.run_cli("status", running=True)
        self.assertTrue(st["agent_running"])
        self.assertTrue(st["autoresume"])
        self.assertTrue(st["pocketlock"])
        self.assertTrue(st["library_scanned"])

    def test_done_persists_and_reset_brings_it_back(self):
        self.assertTrue(self.run_cli("done")[1]["success"])
        self.assertTrue(self.run_cli("status")[1]["done"])
        self.run_cli("reset")
        self.assertFalse(self.run_cli("status")[1]["done"])


if __name__ == "__main__":
    unittest.main()
