"""Đợt D: mức cảnh báo vault_verify/prewarm từ agent_state.json và bố cục bộ đếm ListScreen.
Không cần SDL: gfx giả chỉ ghi lại các lệnh vẽ."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import ui_screens as U

NOW = 1_800_000_000.0
DAY = 86400


def vv(**kw):
    d = {"ts": NOW - 3600, "ok": True, "checked": 120, "bad_objects": [], "missing_objects": 0, "partial": False}
    d.update(kw)
    return {"vault_verify": d}


class TestVaultVerifyStatus(unittest.TestCase):
    def test_never_checked_is_silent(self):
        for st in ({}, {"vault_verify": None}, {"vault_verify": {}}, {"vault_verify": "x"}, {"vault_verify": {"ok": "yes"}}, None, []):
            r = U.vault_verify_status(st, NOW)
            self.assertEqual(r["level"], "none", st)
            self.assertEqual(r["line"], "", st)

    def test_ok_recent_is_green(self):
        r = U.vault_verify_status(vv(), NOW)
        self.assertEqual((r["level"], r["color"]), ("ok", "green"))

    def test_bad_is_red_and_counts_bad_plus_missing(self):
        r = U.vault_verify_status(vv(ok=False, bad_objects=["a1", "b2"], missing_objects=3), NOW)
        self.assertEqual((r["level"], r["color"], r["count"]), ("bad", "red", 5))
        self.assertEqual(r["line"], "!! Kiểm tra toàn vẹn: 5 object hỏng/thiếu — xem SaveVault")
        self.assertEqual(r["bad"], ["a1", "b2"])

    def test_age_boundary_14_days(self):
        self.assertEqual(U.vault_verify_status(vv(ts=NOW - 14 * DAY - 3600), NOW)["level"], "ok")   # 14 ngày + 1 giờ: chưa quá
        r = U.vault_verify_status(vv(ts=NOW - 15 * DAY), NOW)
        self.assertEqual((r["level"], r["color"]), ("stale", "yellow"))
        self.assertTrue(r["line"].startswith("! "))

    def test_bad_beats_stale(self):
        r = U.vault_verify_status(vv(ok=False, ts=NOW - 60 * DAY, missing_objects=1), NOW)
        self.assertEqual(r["level"], "bad")

    def test_missing_fields_do_not_raise(self):
        r = U.vault_verify_status({"vault_verify": {"ok": True}}, NOW)
        self.assertEqual(r["level"], "ok")
        self.assertIsNone(r["ts"])
        r = U.vault_verify_status({"vault_verify": {"ok": False}}, NOW)
        self.assertEqual((r["level"], r["count"]), ("bad", 0))
        r = U.vault_verify_status({"vault_verify": {"ok": False, "bad_objects": "zzz", "missing_objects": "n", "ts": "x"}}, NOW)
        self.assertEqual(r["level"], "bad")

    def test_future_ts_is_not_stale(self):
        self.assertEqual(U.vault_verify_status(vv(ts=NOW + 5 * DAY), NOW)["level"], "ok")


class TestAgentStateFile(unittest.TestCase):
    def test_read_state_tolerates_missing_and_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(U.read_agent_state(d), {})
            (Path(d) / "agent_state.json").write_text("{không phải json", encoding="utf-8")
            self.assertEqual(U.read_agent_state(d), {})
            (Path(d) / "agent_state.json").write_text("[1,2]", encoding="utf-8")
            self.assertEqual(U.read_agent_state(d), {})
            (Path(d) / "agent_state.json").write_text(json.dumps(vv(ok=False)), encoding="utf-8")
            self.assertFalse(U.read_agent_state(d)["vault_verify"]["ok"])
        self.assertEqual(U.read_agent_state(None), {})

    def test_prewarm_line(self):
        self.assertEqual(U.prewarm_line({}), "")
        self.assertEqual(U.prewarm_line({"prewarm": {"ts": 1}}), "")
        self.assertIn("xong", U.prewarm_line({"prewarm": {"done": True, "hashed": 7, "remaining": 0}}))
        self.assertIn("còn 4 file", U.prewarm_line({"prewarm": {"done": False, "remaining": 4}}))


def home_cards(state):
    with tempfile.TemporaryDirectory() as d:
        if state is not None:
            (Path(d) / "agent_state.json").write_text(json.dumps(state), encoding="utf-8")
        dev = {"battery": {}, "thermal": {}, "status": "green", "warnings": []}
        st = {"primary": {}, "secondary": None, "status": "green", "warnings": []}
        app = SimpleNamespace(
            home_data={"readiness": {"device": dev, "storage": st}, "snapshots": [{"id": "s1", "created_at": "t"}], "recovery": None},
            vault=object(), engine_error=None, app_data=d, report_summary=lambda: None)
        cards = U.HomeScreen(app).cards()
    return cards[2], cards[3]


class TestHomeSaveCard(unittest.TestCase):
    def setUp(self):
        # offsite.json không có -> vàng; khoá nó lại để mức viền chỉ phụ thuộc vault_verify
        self._orig = U.offsite_status
        U.offsite_status = lambda app_data, now=None: {"line": "+ ok", "color": "green"}

    def tearDown(self):
        U.offsite_status = self._orig

    def test_no_state_adds_no_warning(self):
        save, _ = home_cards(None)
        self.assertEqual(save[2], "green")
        self.assertFalse(any("toàn vẹn" in l.lower() for l in save[1]))

    def test_bad_vault_turns_card_red_with_line(self):
        save, _ = home_cards(vv(ok=False, bad_objects=["x"], missing_objects=1))
        self.assertEqual(save[2], "red")
        self.assertIn("!! Kiểm tra toàn vẹn: 2 object hỏng/thiếu — xem SaveVault", save[1])

    def test_stale_vault_turns_card_yellow(self):
        save, _ = home_cards(vv(ts=time.time() - 30 * DAY))
        self.assertEqual(save[2], "yellow")

    def test_prewarm_shown_on_library_card_only_when_present(self):
        _, lib = home_cards({"prewarm": {"done": True, "hashed": 5, "remaining": 0, "ts": 1}})
        self.assertTrue(any("Băm trước" in l for l in lib[1]))
        _, lib = home_cards({})
        self.assertFalse(any("Băm trước" in l for l in lib[1]))


class FakeGfx:
    def __init__(self):
        self.texts = []

    def text(self, s, font, color, x, y):
        self.texts.append((s, font, x, y))

    def width(self, s, font):
        return 8 * len(s)

    def fit(self, s, font, w):
        return s

    def wrap(self, s, font, w):
        return [s]

    def fill(self, *a):
        pass

    def outline(self, *a):
        pass

    def clear(self, *a):
        pass


class TestListCounterLayout(unittest.TestCase):
    SMALL_H = 22   # chiều cao dòng chữ cỡ small ~ bước dòng của UI

    def test_counter_never_overlaps_footer(self):
        items = [U.Item(f"mục {i}", explain=["x"]) for i in range(40)]
        scr = U.ListScreen(SimpleNamespace(), "T", lambda: ([], items))
        scr.sel = 30
        g = FakeGfx()
        scr.render(g)
        counter = [t for t in g.texts if t[0] == "31/40"]
        self.assertEqual(len(counter), 1, "phải có bộ đếm khi danh sách dài hơn một trang")
        footer_top = U.WIN_H - 30   # App.render vẽ chân màn hình ở đây
        self.assertLessEqual(counter[0][3] + self.SMALL_H, footer_top)


if __name__ == "__main__":
    unittest.main()
