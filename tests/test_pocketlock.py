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
        self.order = []

    def grab(self, on):
        self.grab_calls.append(on)
        self.order.append("grab" if on else "ungrab")
        self.grabbed = on

    def release_stuck(self):                  # như EvdevPad: ghi lại "nhấn", nhả độc quyền, ghi "nhả"
        self.order.append("release_stuck")
        self.grab(False)

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
        self.haptics = []
        self.lock.haptic = self.haptics.append

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
    """Khoá không có chữ trên màn (Leaf không cho app vẽ đè): rung giao diện của Leaf (IPC "rumble", theo cài đặt
    "rung giao diện" của người dùng, chạy cả ngoài game) là dấu hiệu duy nhất."""
    def setUp(self):
        super().setUp()
        self.lock.step()
        self.wake()

    def test_locking_on_wake_does_not_buzz(self):
        self.assertEqual(self.haptics, [])

    def test_press_while_locked_buzzes_blocked_rate_limited(self):
        self.key(304, 1)
        self.assertEqual(self.haptics, ["blocked"])
        self.key(304, 0)
        self.key(305, 1)
        self.assertEqual(self.haptics, ["blocked"])            # chưa đủ BUZZ_EVERY_S
        self.advance(PL.BUZZ_EVERY_S + 0.1)
        self.key(305, 1)
        self.assertEqual(self.haptics, ["blocked", "blocked"])

    def test_releases_and_repeats_do_not_buzz(self):
        self.key(304, 0)
        self.key(304, 2)
        self.assertEqual(self.haptics, [])

    def test_hat_press_while_locked_also_buzzes(self):
        self.pad().events.append((PL.EV_ABS, PL.ABS_HAT0Y, 1))
        self.lock.step()
        self.assertEqual(self.haptics, ["blocked"])

    def test_unlock_confirms_with_commit(self):
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(1.1)
        self.assertFalse(self.lock.locked)
        self.assertEqual(self.haptics[-1], "commit")


class StuckInput(Base):
    """D-pad bấm đúng lúc khoá bật: app đã nhận "nhấn" nhưng "nhả" bị khoá giữ lại -> sau khi mở khoá app tưởng nút
    vẫn giữ và tự lướt liên tục (lỗi gặp trên máy thật). Mở khoá phải trả lại trạng thái nhả cho app."""
    def test_unlock_releases_stuck_input_before_closing(self):
        self.lock.step()
        self.wake()
        pad = self.pad()
        self.key(PL.BTN_SELECT, 1)
        self.key(PL.BTN_START, 1)
        self.advance(1.1)
        self.assertEqual(pad.order, ["grab", "release_stuck", "ungrab"])
        self.assertTrue(pad.closed)


class EvdevSequence(unittest.TestCase):
    """Thứ tự ghi sự kiện của EvdevPad (không cần thiết bị thật): nút đang giữ lúc khoá được "nhấn" lại khi còn giữ độc
    quyền (chỉ Agent nhận, để kernel coi là đang nhấn), rồi nhả độc quyền, rồi "nhả" (mọi app nhận)."""
    class Pad(PL.EvdevPad):
        def __init__(self, keys_down, hats):
            self.fd = -1
            self.effects = {}
            self._keys_down, self._hats = keys_down, hats
            self.log = []

        def _query_state(self):
            return set(self._keys_down), dict(self._hats)

        def _ioctl_grab(self, on):
            self.log.append(("grab", on))

        def _write_events(self, evs):
            self.log.append(("write", list(evs)))

    def test_snapshot_at_grab_then_restore_release(self):
        pad = self.Pad({PL.BTN_START}, {PL.ABS_HAT0Y: -1, PL.ABS_HAT0X: 0})
        pad.grab(True)
        pad.release_stuck()
        self.assertEqual(pad.log[0], ("grab", True))
        self.assertEqual(pad.log[1], ("write", [(PL.EV_KEY, PL.BTN_START, 1), (PL.EV_ABS, PL.ABS_HAT0Y, -1),
                                                (PL.EV_SYN, 0, 0)]))
        self.assertEqual(pad.log[2], ("grab", False))
        self.assertEqual(pad.log[3], ("write", [(PL.EV_KEY, PL.BTN_START, 0), (PL.EV_ABS, PL.ABS_HAT0Y, 0),
                                                (PL.EV_SYN, 0, 0)]))

    def test_nothing_held_writes_nothing(self):
        pad = self.Pad(set(), {PL.ABS_HAT0X: 0, PL.ABS_HAT0Y: 0})
        pad.grab(True)
        pad.release_stuck()
        self.assertEqual(pad.log, [("grab", True), ("grab", False)])


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
