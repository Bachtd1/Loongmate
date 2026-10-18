"""Logic thuần (không SDL) của các màn đợt B trong engine/ui_screens.py."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import jawaka
from engine import ui_screens as ui
from engine.autobackup import PidLock


class FakeProbe:
    primary_path = secondary_path = userdata_path = None

    def get_readiness_summary(self):
        return {}


def make_app():
    return ui.App(FakeProbe(), None, None, None, None, None, None)


class TestChartMath(unittest.TestCase):
    def test_bucketize_empty_buckets_are_none_not_zero(self):
        rows = [{"ts": 100, "v": 10}, {"ts": 101, "v": 30}, {"ts": 250, "v": 50}]
        out = ui.bucketize(rows, 100, 200, 4, lambda r: r["v"], ui._avg)
        self.assertEqual(out[0], 20.0)          # ts 100,101 -> cột 0
        self.assertIsNone(out[1])               # không có mẫu: KHÔNG điền 0
        self.assertIsNone(out[2])
        self.assertEqual(out[3], 50)            # ts 250 -> (250-100)*4/200 = cột 3

    def test_bucketize_ignores_out_of_range_and_missing(self):
        rows = [{"ts": 99, "v": 1}, {"ts": 301, "v": 2}, {"ts": 150, "v": None}, {"ts": 300, "v": 7}]
        out = ui.bucketize(rows, 100, 200, 2, lambda r: r["v"], max)
        self.assertEqual(out, [None, 7])        # ts=300 (mép cuối) rơi vào cột cuối

    def test_peak_temp_uses_max_of_available_sensors(self):
        self.assertEqual(ui.peak_temp({"soc_c": 50.0, "gpu_c": 61.5, "batt_c": None}), 61.5)
        self.assertIsNone(ui.peak_temp({"soc_c": None, "gpu_c": None, "batt_c": None}))

    def test_summary_says_not_enough_data_instead_of_inventing_drain(self):
        s = {"samples": 2, "first_ts": 0, "last_ts": 60, "drain_pct_per_hour": None, "discharge_hours": 0.0,
             "temp": {"soc": None, "gpu": None, "batt": None}, "over_70_events": 0, "over_70_samples": 0}
        text = "\n".join(ui.summary_lines(s))
        self.assertIn("chưa đủ dữ liệu", text)
        self.assertIn("không đọc được", text)


class TestAgentRunning(unittest.TestCase):
    def test_none_false_true_and_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(ui.App.agent_running(d))                  # chưa từng có file khoá
            lock_path = Path(d) / "agent.lock"
            lock_path.write_text("12345")
            before = lock_path.read_bytes()
            self.assertFalse(ui.App.agent_running(d))                   # có file, không ai giữ
            self.assertEqual(lock_path.read_bytes(), before)            # chỉ đọc: KHÔNG ghi pid của UI
            holder = PidLock(lock_path)
            self.assertTrue(holder.acquire())
            try:
                self.assertTrue(ui.App.agent_running(d))                # agent đang giữ khoá
            finally:
                holder.release()
            self.assertFalse(ui.App.agent_running(d))


class TestFinishChange(unittest.TestCase):
    def _run(self, app):
        app.wait_task()
        return "\n".join(app.dialog.lines)

    def test_env_switch_never_calls_jawaka(self):
        app = make_app()
        with mock.patch.dict(os.environ, {ui.NO_RESCAN_ENV: "1"}), \
                mock.patch.object(jawaka, "rescan_available", side_effect=AssertionError("không được gọi")), \
                mock.patch.object(jawaka, "request_rescan", side_effect=AssertionError("không được gọi")):
            app.finish_change("T", ["+ xong"], True)
            text = "\n".join(app.dialog.lines)
        self.assertIn(ui.RESCAN_REMINDER, text)

    def test_unchanged_library_does_not_rescan(self):
        app = make_app()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(jawaka, "rescan_available", side_effect=AssertionError("không được gọi")):
            os.environ.pop(ui.NO_RESCAN_ENV, None)
            app.finish_change("T", ["không đổi gì"], False)
        self.assertNotIn(ui.RESCAN_REMINDER, "\n".join(app.dialog.lines))

    def test_success_replaces_manual_reminder_with_real_result(self):
        app = make_app()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(jawaka, "rescan_available", return_value={"available": True, "reason": ""}), \
                mock.patch.object(jawaka, "request_rescan", return_value={"ok": True, "message": "Quét xong: 7 game, 1 app.", "waited_s": 0.5}) as rq:
            os.environ.pop(ui.NO_RESCAN_ENV, None)
            app.finish_change("T", ["+ xong"], True)
            text = self._run(app)
        rq.assert_called_once()
        self.assertIn("Quét xong: 7 game, 1 app.", text)
        self.assertNotIn(ui.RESCAN_REMINDER, text)

    def test_unavailable_keeps_reminder_with_real_reason(self):
        app = make_app()
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(jawaka, "rescan_available", return_value={"available": False, "reason": "socket vắng"}), \
                mock.patch.object(jawaka, "request_rescan", side_effect=AssertionError("không được gọi")):
            os.environ.pop(ui.NO_RESCAN_ENV, None)
            app.finish_change("T", ["+ xong"], True)
            text = self._run(app)
        self.assertIn(ui.RESCAN_REMINDER, text)
        self.assertIn("socket vắng", text)


if __name__ == "__main__":
    unittest.main()
