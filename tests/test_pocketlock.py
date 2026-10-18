"""Khoá khi thức dậy: máy để trong túi bị cấn nút nguồn thì thức, các nút khác bị đè làm máy bấm lung tung. Agent giữ
độc quyền tay cầm ảo của Leaf ngay khi máy thức, chỉ nhả khi giữ Select + Start 1 giây; không mở thì cho máy ngủ lại."""
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import pocketlock as PL  # noqa: E402

PROC_DEVICES = """I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="rk805 pwrkey"
P: Phys=rk805_pwrkey/input0
S: Sysfs=/devices/platform/fdd40000.i2c/i2c-0/0-0020/rk805-pwrkey/input/input0
H: Handlers=kbd event0 dmcfreq

I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="Loong Gamepad"
P: Phys=loong1_joypad/input0
S: Sysfs=/devices/platform/loong1_joypad/input/input4
H: Handlers=kbd event4 dmcfreq

I: Bus=0006 Vendor=0001 Product=0001 Version=0001
N: Name="Loong Gamepad"
P: Phys=
S: Sysfs=/devices/virtual/input/input12
H: Handlers=kbd event5 dmcfreq
"""


class FakePad:
    def __init__(self):
        self.grabbed = False
        self.closed = False
        self.events = []
        self.grab_calls = []
        self.buzzes = []

    def rumble(self, ms):
        self.buzzes.append(ms)

    def grab(self, on):
        self.grab_calls.append(on)
        self.grabbed = on

    def read(self, timeout):
        ev, self.events = self.events, []
        return ev

    def close(self):
        self.grabbed = False
        self.closed = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_lock_"))
        self.t = [100.0]
        self.susp = [5000.0]               # giây máy đã ngủ (CLOCK_BOOTTIME - CLOCK_MONOTONIC)
        self.pads = []
        self.sent = []
        self.logs = []
        self.on = True

        def open_pad():
            p = FakePad()
            self.pads.append(p)
            return p

        self.lock = PL.PocketLock(self.tmp, open_pad=open_pad, request=self.sent.append,
                                  clock=lambda: self.t[0], suspended_s=lambda: self.susp[0],
                                  log=self.logs.append, is_enabled=lambda: self.on)
        self.lock.sleep = lambda s: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pad(self):
        return self.pads[-1]

    def wake(self, slept=60.0):
        self.susp[0] += slept
        self.lock.step()

    def key(self, code, value):
        self.pad().events.append((PL.EV_KEY, code, value))
        self.lock.step()

    def advance(self, s):
        self.t[0] += s
        self.lock.step()


class FindPad(unittest.TestCase):
    def test_picks_the_virtual_gamepad_not_the_physical_one(self):
        self.assertEqual(PL.find_virtual_pad(PROC_DEVICES), "/dev/input/event5")

    def test_none_when_leaf_has_no_virtual_pad(self):
        self.assertIsNone(PL.find_virtual_pad(PROC_DEVICES.split("\n\nI: Bus=0006")[0]))


class Wake(Base):
    def test_first_step_only_records_baseline(self):
        self.lock.step()
        self.assertFalse(self.lock.locked)
        self.assertEqual(self.pads, [])

    def test_locks_after_sleep_and_grabs_the_pad(self):
        self.lock.step()
        self.wake()
        self.assertTrue(self.lock.locked)
        self.assertTrue(self.pad().grabbed)

    def test_short_gap_is_not_a_wake(self):
        self.lock.step()
        self.susp[0] += 0.3                    # sai số đồng hồ, không phải ngủ
        self.lock.step()
        self.assertFalse(self.lock.locked)

    def test_disabled_never_locks(self):
        self.on = False
        self.lock.step()
        self.wake()
        self.assertFalse(self.lock.locked)
        self.assertEqual(self.pads, [])

    def test_wake_from_screen_off_standby_also_locks(self):
        """Tắt màn do để lâu (standby, chưa ngủ hẳn): bấm nút nào Leaf cũng bật màn; Agent đọc dòng log đó."""
        self.lock.step()
        self.lock.feed_line("2026-10-18 17:40:01 INFO standby: wake on input")
        self.lock.step()
        self.assertTrue(self.lock.locked)


class Unlock(Base):
    def setUp(self):
        super().setUp()
        self.lock.step()
        self.wake()

    def test_hold_select_and_start_one_second_unlocks(self):
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(0.5)
        self.assertTrue(self.lock.locked)
        self.advance(0.6)
        self.assertFalse(self.lock.locked)
        self.assertTrue(self.pad().closed)
        self.assertEqual(self.pad().grab_calls, [True, False])

    def test_releasing_early_does_not_unlock(self):
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(0.5)
        self.key(PL.BTN_START, 0)
        self.advance(1.0)
        self.assertTrue(self.lock.locked)

    def test_other_buttons_do_nothing(self):
        for code in (PL.BTN_SELECT, 304, 305, 316):
            self.key(code, 1)
        self.advance(2.0)
        self.assertTrue(self.lock.locked)

    def test_autorepeat_value_two_counts_as_held(self):
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.key(PL.BTN_START, 2)
        self.advance(1.1)
        self.assertFalse(self.lock.locked)


class Feedback(Base):
    """Khoá không có chữ trên màn (Leaf không cho app vẽ đè): rung là dấu hiệu duy nhất."""
    def setUp(self):
        super().setUp()
        self.lock.step()
        self.wake()

    def test_locking_on_wake_does_not_buzz(self):
        self.assertEqual(self.pad().buzzes, [])

    def test_press_while_locked_buzzes_rate_limited(self):
        self.key(304, 1)
        self.assertEqual(len(self.pad().buzzes), 1)
        self.key(304, 0)
        self.key(305, 1)
        self.assertEqual(len(self.pad().buzzes), 1)        # chưa đủ BUZZ_EVERY_S
        self.advance(PL.BUZZ_EVERY_S + 0.1)
        self.key(305, 1)
        self.assertEqual(len(self.pad().buzzes), 2)

    def test_releases_and_repeats_do_not_buzz(self):
        self.key(304, 0)
        self.key(304, 2)
        self.assertEqual(self.pad().buzzes, [])

    def test_unlock_gives_a_longer_buzz_before_release(self):
        pad = self.pad()
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(1.1)
        self.assertFalse(self.lock.locked)
        self.assertEqual(pad.buzzes[-1], PL.UNLOCK_BUZZ_MS)
        self.assertGreater(PL.UNLOCK_BUZZ_MS, PL.PRESS_BUZZ_MS)

    def test_pad_without_rumble_still_works(self):
        pad = self.pad()
        def no_ff(ms):
            raise OSError("no FF")
        pad.rumble = no_ff
        self.key(304, 1)
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(1.1)
        self.assertFalse(self.lock.locked)


class Resleep(Base):
    def setUp(self):
        super().setUp()
        self.lock.step()
        self.wake()

    def test_not_unlocked_in_time_puts_device_back_to_sleep_once(self):
        self.advance(PL.RESLEEP_S - 1)
        self.assertEqual(self.sent, [])
        self.advance(1.5)
        self.assertEqual(self.sent, [{"type": "platform-action", "action": "sleep"}])
        self.advance(1.0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.lock.locked)            # vẫn khoá: lần thức sau phải mở khoá

    def test_next_wake_restarts_the_countdown(self):
        self.advance(PL.RESLEEP_S + 1)
        self.wake()
        self.advance(PL.RESLEEP_S - 1)
        self.assertEqual(len(self.sent), 1)
        self.advance(1.5)
        self.assertEqual(len(self.sent), 2)

    def test_sleep_refused_rearms_later(self):
        """Leaf hoãn ngủ (đang sạc / service giữ quyền chặn ngủ): không thức lại thì thử lại sau, vẫn giữ khoá."""
        self.advance(PL.RESLEEP_S + 1)
        self.advance(PL.RESLEEP_S + 1)
        self.assertEqual(len(self.sent), 2)


class FailOpen(Base):
    def test_turning_feature_off_while_locked_releases(self):
        self.lock.step()
        self.wake()
        self.on = False
        self.advance(PL.CONFIG_RECHECK_S + 1)
        self.assertFalse(self.lock.locked)
        self.assertTrue(self.pad().closed)

    def test_pad_missing_does_not_crash_and_stays_unlocked(self):
        def broken():
            raise OSError("no device")
        self.lock.open_pad = broken
        self.lock.step()
        self.wake()
        self.assertFalse(self.lock.locked)
        self.assertTrue(any("khong giu duoc" in m for m in self.logs))

    def test_stop_always_releases(self):
        self.lock.step()
        self.wake()
        self.lock.stop()
        self.assertTrue(self.pad().closed)
        self.assertFalse(self.lock.locked)


class RunThread(Base):
    def test_background_loop_locks_on_wake_and_releases_on_stop(self):
        import threading
        import time as _t
        self.lock.clock = _t.monotonic
        stop = threading.Event()
        th = threading.Thread(target=self.lock.run, args=(stop,), daemon=True)
        th.start()
        _t.sleep(0.3)
        self.susp[0] += 60
        for _ in range(50):
            if self.lock.locked:
                break
            _t.sleep(0.05)
        self.assertTrue(self.lock.locked)
        stop.set()
        th.join(2)
        self.assertFalse(th.is_alive())
        self.assertTrue(self.pad().closed)
        self.assertFalse(self.lock.locked)


class Cli(unittest.TestCase):
    def test_status_on_off(self):
        import contextlib
        import io
        import json
        import engine.cli as CLI
        tmp = Path(tempfile.mkdtemp(prefix="lm_lock_cli_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ud = tmp / "ud"
        ud.mkdir()

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = CLI.main(["--primary", str(tmp), "--userdata", str(ud), "--json", "pocketlock", *argv])
            return code, json.loads(out.getvalue())

        code, st = run("status")
        self.assertEqual(code, 0)
        self.assertFalse(st["enabled"])
        self.assertFalse(st["agent_running"])
        self.assertTrue(run("on")[1]["enabled"])
        self.assertTrue(run("status")[1]["enabled"])
        self.assertFalse(run("off")[1]["enabled"])


class Config(unittest.TestCase):
    def test_default_off_and_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(PL.enabled(tmp))
            self.assertTrue(PL.set_enabled(tmp, True)["enabled"])
            self.assertTrue(PL.enabled(tmp))
            PL.set_enabled(tmp, False)
            self.assertFalse(PL.enabled(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
