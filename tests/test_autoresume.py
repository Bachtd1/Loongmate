"""Tự tiếp tục qua Agent: đọc dòng log launch-game của jawakad, chờ RetroArch sẵn sàng, nạp đúng state Leaf sẽ chọn."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import autoresume as AR  # noqa: E402

ROM = "/mnt/sdcard/Roms/GBA/Hero Quest.gba"


def launch_line(resume="none", rom=ROM):
    return f"2026-10-17 20:50:01 INFO launch-game requested system=GBA rom={rom} core=(default) resume={resume}"


def commit_line(kind="RETROARCH"):
    return f"2026-10-17 21:03:47 INFO life1: active launch committed launch_id=1792245827-47-1410-1-27 source=primary kind={kind}"


class FakeDaemon:
    def __init__(self):
        self.session = {"type": "retroarch-session", "active": False, "command_ok": False}
        self.load_ok = True
        self.calls = []

    def __call__(self, req):
        self.calls.append(req)
        if req["type"] == "retroarch-session":
            return dict(self.session)
        if req == {"type": "retroarch-action", "action": "load-state", "value": req.get("value")}:
            return {"type": "ok" if self.load_ok else "error", "action": "load-state"}
        return {"type": "error"}


class TestAutoResume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_ar_"))
        self.states = self.tmp / "States"
        (self.states / "mGBA").mkdir(parents=True)
        self.t = [0.0]
        self.d = FakeDaemon()
        self.logs = []
        self.on = True
        self.ar = AR.AutoResume(self.tmp, request=self.d, clock=lambda: self.t[0], log=self.logs.append,
                                active_game=lambda: {"states_path": str(self.states)}, is_enabled=lambda: self.on)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self, name, age=0.0):
        p = self.states / "mGBA" / name
        p.write_bytes(b"s")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def ready(self):
        self.d.session = {"type": "retroarch-session", "active": True, "command_ok": True, "rom_path": ROM,
                          "core_config_folder": "mGBA"}

    def loads(self):
        return [c["value"] for c in self.d.calls if c.get("action") == "load-state"]

    def test_prefers_switcher_slot_99(self):
        self.state("Hero Quest.state3", age=0)
        self.state("Hero Quest.state99", age=100)
        self.assertEqual(AR.find_resume_slot(self.states, "mGBA", ROM)[0], 99)

    def test_else_newest_state_any_slot_and_ignores_thumbnails_and_other_games(self):
        self.state("Hero Quest.state", age=300)
        self.state("Hero Quest.state.auto", age=50)
        self.state("Hero Quest.state2", age=100)
        self.state("Hero Quest.state2.png", age=0)
        self.state("Hero Quest 2.state5", age=0)
        self.assertEqual(AR.find_resume_slot(self.states, "mGBA", ROM)[0], AR.AUTO_SLOT)
        self.assertIsNone(AR.find_resume_slot(self.states, "Snes9x", ROM))
        self.assertIsNone(AR.find_resume_slot(self.states, "../x", ROM))

    def test_games_tab_launch_waits_for_retroarch_then_loads(self):
        self.state("Hero Quest.state99")
        self.ar.feed_line(launch_line("none"))
        self.t[0] = 60
        self.assertIsNone(self.ar.tick())                    # lệnh đang xếp hàng (launcher chưa thoát): chưa đếm giờ
        self.ar.feed_line(commit_line())
        self.assertIsNone(self.ar.tick())                    # RetroArch chưa chạy
        self.d.session = {"type": "retroarch-session", "active": True, "command_ok": False, "rom_path": ROM}
        self.assertIsNone(self.ar.tick())                    # đang khởi động, chưa nhận lệnh
        self.ready()
        self.d.load_ok = False
        self.assertIsNone(self.ar.tick())                    # core chưa nạp được: thử lại
        self.d.load_ok = True
        r = self.ar.tick()
        self.assertEqual((r["outcome"], r["slot"]), ("loaded", 99))
        self.assertEqual(self.loads(), [99, 99])
        self.assertIsNone(self.ar.pending)

    def test_recents_launch_is_left_to_leaf(self):
        self.state("Hero Quest.state99")
        self.ready()
        self.ar.feed_line(launch_line("switcher-latest"))
        self.assertIsNone(self.ar.tick())
        self.assertEqual(self.loads(), [])

    def test_disabled_or_no_state_or_timeout_does_nothing(self):
        self.ready()
        self.on = False
        self.ar.feed_line(launch_line())
        self.assertIsNone(self.ar.pending)
        self.on = True
        self.ar.feed_line(launch_line())
        self.ar.feed_line(commit_line())
        self.assertEqual(self.ar.tick()["outcome"], "no_state")
        self.state("Hero Quest.state99")
        self.d.session = {"type": "retroarch-session", "active": False}
        self.ar.feed_line(launch_line())
        self.ar.feed_line(commit_line())
        self.t[0] = AR.WAIT_S + 1
        self.assertEqual(self.ar.tick()["outcome"], "timeout")
        self.assertEqual(self.loads(), [])

    def test_primary_card_launch_logs_relative_rom_path(self):
        """Máy thật (Leaf 0.13.0): game trên thẻ Leaf được log là 'rom=Roms/SNES/...' (tương đối) còn retroarch-session
        báo '/mnt/sdcard/Roms/SNES/...' (tuyệt đối). Trước đây so chuỗi y nguyên nên mọi game SD1 bị coi là 'game khác'."""
        self.state("Hero Quest.state99")
        self.ready()
        self.ar.feed_line(launch_line(rom="Roms/GBA/Hero Quest.gba"))
        self.ar.feed_line(commit_line())
        r = self.ar.tick()
        self.assertEqual((r["outcome"], r["slot"]), ("loaded", 99))
        self.assertEqual(self.loads(), [99])

    def test_relative_rom_without_active_game_uses_session_path_for_states(self):
        self.state("Hero Quest.state99")
        self.ready()
        self.d.session["rom_path"] = str(self.tmp / "Roms" / "GBA" / "Hero Quest.gba").replace("\\", "/")
        self.ar.active_game = lambda: None
        self.ar.feed_line(launch_line(rom="Roms/GBA/Hero Quest.gba"))
        self.ar.feed_line(commit_line())
        self.assertEqual(self.ar.tick()["outcome"], "loaded")

    def test_relative_rom_does_not_match_a_different_game(self):
        self.state("Hero Quest.state99")
        self.ready()
        self.d.session["rom_path"] = "/mnt/sdcard/Roms/GBA/Super Hero Quest.gba"
        self.ar.feed_line(launch_line(rom="Roms/GBA/Hero Quest.gba"))
        self.ar.feed_line(commit_line())
        self.assertEqual(self.ar.tick()["outcome"], "other_game")
        self.assertEqual(self.loads(), [])

    def test_same_rom(self):
        self.assertTrue(AR.same_rom("Roms/SNES/EarthBound (USA).smc", "/mnt/sdcard/Roms/SNES/EarthBound (USA).smc"))
        self.assertTrue(AR.same_rom("/media/sdcard1/Roms/PS/A.cue", "/media/sdcard1/Roms/PS/A.cue"))
        self.assertFalse(AR.same_rom("/media/sdcard1/Roms/PS/A.cue", "/mnt/sdcard/Roms/PS/A.cue"))
        self.assertFalse(AR.same_rom("Roms/GBA/A.gba", "/mnt/sdcard/Roms/GBA/BA.gba"))
        self.assertFalse(AR.same_rom("Roms/GBA/A.gba", None))

    def test_other_game_running_is_not_touched(self):
        self.state("Hero Quest.state99")
        self.ready()
        self.d.session["rom_path"] = "/mnt/sdcard/Roms/GBA/Other.gba"
        self.ar.feed_line(launch_line())
        self.ar.feed_line(commit_line())
        self.assertEqual(self.ar.tick()["outcome"], "other_game")
        self.assertEqual(self.loads(), [])

    def test_queued_launch_dropped_if_never_started_and_standalone_ignored(self):
        self.state("Hero Quest.state99")
        self.ready()
        self.ar.feed_line(launch_line())
        self.t[0] = AR.ARM_MAX_S + 1
        self.assertEqual(self.ar.tick()["outcome"], "not_started")
        self.ar.feed_line(launch_line())
        self.ar.feed_line(commit_line("EMULATOR"))
        self.assertIsNone(self.ar.pending)
        self.assertEqual(self.loads(), [])

    def test_rom_with_spaces_parsed(self):
        rom = "/mnt/sdcard/Roms/SFC/Super Metroid - Cryogenesis v2.0.4.sfc"
        self.ar.feed_line(launch_line(rom=rom))
        self.assertEqual(self.ar.pending["rom"], rom)

    def test_enable_flag_roundtrip(self):
        self.assertFalse(AR.enabled(self.tmp))
        AR.set_enabled(self.tmp, True)
        self.assertTrue(AR.enabled(self.tmp))


REAL_LAUNCH = ("2026-10-18 16:19:58 INFO launch-game requested system=SFC rom=Roms/SNES/EarthBound (USA).smc "
               "core=(default) resume=none")
REAL_COMMIT = ("2026-10-18 16:19:59 INFO life1: active launch committed launch_id=1792315199-173065230-1410-21-443713194 "
               "source=primary kind=RETROARCH")


@unittest.skipUnless(hasattr(__import__("socket"), "AF_UNIX") and os.name == "posix", "cần socket Unix (chạy trên CI Linux)")
class TestCompat(unittest.TestCase):
    """Sau khi cập nhật Leaf: biết cách qua Agent còn chạy không, chỉ báo 'lệch' khi Leaf thật sự đổi cấu trúc."""
    def setUp(self):
        import socket
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_compat_"))
        self.sock_path = str(self.tmp / "jawakad.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.sock_path)
        self.log = self.tmp / "umrk-leaf-session.log"

    def tearDown(self):
        self.srv.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, *lines):
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return AR.compat(self.log, self.sock_path)

    def test_leaf_0_13_log_is_compatible(self):
        r = self.check("x INFO boot", REAL_LAUNCH, REAL_COMMIT)
        self.assertIs(r["ok"], True, r)

    def test_no_game_launched_yet_is_unknown_not_broken(self):
        self.assertIsNone(self.check("x INFO boot")["ok"])

    def test_changed_launch_line_format_is_reported(self):
        r = self.check("2026 INFO launch-game requested game=Roms/SNES/x.smc mode=fresh", REAL_COMMIT)
        self.assertIs(r["ok"], False)
        self.assertIn("launch-game", r["message"])

    def test_missing_commit_line_is_reported(self):
        self.assertIs(self.check(REAL_LAUNCH)["ok"], False)

    def test_missing_socket_is_reported(self):
        self.srv.close()
        os.unlink(self.sock_path)
        r = self.check(REAL_LAUNCH, REAL_COMMIT)
        self.assertIs(r["ok"], False)
        self.assertIn("jawakad", r["message"])

    def test_real_lines_parse(self):
        self.assertEqual(AR._LAUNCH_RE.search(REAL_LAUNCH).group(2), "Roms/SNES/EarthBound (USA).smc")
        self.assertEqual(AR._COMMIT_RE.search(REAL_COMMIT).group(1), "RETROARCH")


if __name__ == "__main__":
    unittest.main()
