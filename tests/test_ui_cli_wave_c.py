"""
Tests đợt C (giao diện + CLI): cảnh báo bản sao ngoài máy, `agent status` chỉ đọc, `battery`,
`update`, dấu "dùng lại hash" của dedupe, và luồng Cập nhật trong UI (không cần SDL).

Mọi test chạy HÀNH VI THẬT: gọi main([...]) / điều khiển App bằng action, trên thẻ giả trong thư mục tạm.
Chứng minh test biết đỏ: chép dự án sang thư mục khác, cố tình làm hỏng (đổi ngưỡng offsite, để
`agent status` dùng PidLock.acquire, ...) rồi chạy lại — xem báo cáo.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.cli as CLI
import engine.telemetry as telemetry
import engine.ui_screens as ui
from engine.autobackup import PidLock
from engine.probe import PlatformProbe

DAY = 86400


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_waveC_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir(parents=True)
        self.ud.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *argv, json_out=True, tty=False):
        base = ["--quiet", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud)]
        if json_out:
            base.append("--json")
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO("")
        stdin.isatty = lambda: tty
        with mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CLI.main(base + list(argv))
        text = out.getvalue()
        return code, text, (json.loads(text) if json_out else None)

    def write_offsite(self, age_days, verified=True, **extra):
        ts = int(time.time() - age_days * DAY)
        d = {"schema": 1, "last_pull_ts": ts, "last_pull_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
             "pc_host": "PC-TEST", "snapshot_ids": ["s1", "s2"], "objects": 7, "verified": verified}
        d.update(extra)
        (self.ud / "offsite.json").write_text(json.dumps(d), encoding="utf-8")


# ------------------------------------------------------------------ B: bản sao ngoài máy
class TestOffsiteLevels(Base):
    NOW = 2_000_000_000.0

    def st(self, age_days, **kw):
        d = {"schema": 1, "last_pull_ts": int(self.NOW - age_days * DAY), "verified": True, "snapshot_ids": [], "objects": 1}
        d.update(kw)
        (self.ud / "offsite.json").write_text(json.dumps(d), encoding="utf-8")
        return ui.offsite_status(self.ud, now=self.NOW)

    def test_01_missing_file_says_install_schedule(self):
        r = ui.offsite_status(self.ud, now=self.NOW)
        self.assertEqual((r["level"], r["color"]), ("missing", "yellow"))
        self.assertEqual(r["message"], "Chưa có bản sao ngoài máy — cài lịch kéo về PC (pc/install_schedule.ps1)")

    def test_02_age_thresholds(self):
        for age, level, color in ((0, "ok", "green"), (2.9, "ok", "green"), (3, "ok", "green"),
                                  (3.2, "stale", "yellow"), (10, "stale", "yellow"), (14, "stale", "yellow"),
                                  (14.2, "old", "red"), (60, "old", "red")):
            r = self.st(age)
            self.assertEqual((r["level"], r["color"]), (level, color), f"tuổi {age} ngày")

    def test_03_message_and_prefix(self):
        r = self.st(5)
        self.assertEqual(r["message"], "Bản sao ngoài máy: 5 ngày trước")
        self.assertTrue(r["line"].startswith("! "))
        r = self.st(20)
        self.assertEqual(r["message"], "Bản sao ngoài máy: 20 ngày trước")
        self.assertTrue(r["line"].startswith("!! "))
        self.assertEqual(self.st(0.2)["message"], "Bản sao ngoài máy: hôm nay")

    def test_04_unverified_is_at_least_yellow(self):
        r = self.st(0.5, verified=False)
        self.assertEqual(r["color"], "yellow")
        self.assertIn("CHƯA xác minh", r["message"])
        self.assertEqual(self.st(0.5, verified="true")["color"], "yellow")   # chỉ True thật mới tính là đã xác minh
        self.assertEqual(self.st(30, verified=False)["color"], "red")          # không hạ mức đỏ xuống vàng

    def test_05_broken_or_future_never_green(self):
        (self.ud / "offsite.json").write_text("{không phải json", encoding="utf-8")
        self.assertEqual(ui.offsite_status(self.ud, now=self.NOW)["level"], "invalid")
        (self.ud / "offsite.json").write_text(json.dumps({"schema": 1, "verified": True}), encoding="utf-8")
        self.assertEqual(ui.offsite_status(self.ud, now=self.NOW)["level"], "invalid")
        r = self.st(-2)
        self.assertEqual((r["level"], r["color"]), ("future", "yellow"))

    def test_06_home_save_card_border_follows_level(self):
        snaps = [{"id": "snap1", "created_at": "2026-10-01 10:00:00"}]
        app = SimpleNamespace(vault=object(), engine_error=None, app_data=self.ud, report_summary=lambda: None,
                              home_data={"readiness": {"device": {"battery": {}, "thermal": {}, "status": "green"},
                                                       "storage": {"status": "green"}},
                                         "snapshots": snaps, "recovery": None})
        # readiness giả thiếu khoá khiến c1/c2 vỡ: chỉ lấy thẻ SAVE nên gọi qua cards() với bản vá nhẹ.
        with mock.patch.object(ui, "battery_line", lambda b: "Pin"), mock.patch.object(ui, "thermal_line", lambda t: "Nhiệt"), \
                mock.patch.object(ui, "storage_lines", lambda s: ["SD"]):
            home = ui.HomeScreen(app)

            def save_card():
                return home.cards()[2]
            title, lines, color = save_card()                      # chưa có offsite.json
            self.assertEqual(color, "yellow")
            self.assertTrue(any("Chưa có bản sao ngoài máy" in ln for ln in lines))
            self.write_offsite(1)
            self.assertEqual(save_card()[2], "green")
            self.write_offsite(5)
            self.assertEqual(save_card()[2], "yellow")
            self.write_offsite(20)
            title, lines, color = save_card()
            self.assertEqual(color, "red")
            self.assertTrue(any("20 ngày trước" in ln for ln in lines))


class TestReadinessOffsite(Base):
    def test_01_json_has_offsite_block(self):
        code, _, data = self.run_cli("readiness")
        self.assertEqual(data["offsite"]["level"], "missing")
        self.write_offsite(16)
        _, _, data = self.run_cli("readiness")
        self.assertEqual((data["offsite"]["level"], data["offsite"]["color"], data["offsite"]["age_days"]), ("old", "red", 16))
        self.assertEqual(data["offsite"]["pc_host"], "PC-TEST")

    def test_02_text_shows_message(self):
        _, text, _ = self.run_cli("readiness", json_out=False)
        self.assertIn("Chưa có bản sao ngoài máy — cài lịch kéo về PC (pc/install_schedule.ps1)", text)
        self.write_offsite(5)
        _, text, _ = self.run_cli("readiness", json_out=False)
        self.assertIn("Bản sao ngoài máy: 5 ngày trước", text)


# ------------------------------------------------------------------ agent status chỉ đọc
class TestAgentStatusReadOnly(Base):
    def listing(self):
        # chỉ so FILE: SaveVault() của main() luôn tạo sẵn vài thư mục rỗng, không liên quan tới khoá agent
        return sorted(p.relative_to(self.ud).as_posix() for p in self.ud.rglob("*") if p.is_file())

    def test_01_no_lock_file_is_not_created(self):
        before = self.listing()
        code, _, data = self.run_cli("agent", "status")
        self.assertEqual(code, 0)
        self.assertFalse(data["running"])
        self.assertFalse((self.ud / "agent.lock").exists(), "agent status không được tạo agent.lock")
        self.assertEqual(self.listing(), before)

    def test_02_existing_unheld_lock_is_left_untouched(self):
        lock = self.ud / "agent.lock"
        lock.write_bytes(b"4242")
        mtime = lock.stat().st_mtime_ns
        _, _, data = self.run_cli("agent", "status")
        self.assertFalse(data["running"])
        self.assertEqual(lock.read_bytes(), b"4242", "không được ghi pid của CLI vào agent.lock")
        self.assertEqual(lock.stat().st_mtime_ns, mtime)

    def test_03_running_agent_is_still_detected(self):
        holder = PidLock(self.ud / "agent.lock")
        self.assertTrue(holder.acquire())
        self.addCleanup(holder.release)
        _, _, data = self.run_cli("agent", "status")
        self.assertTrue(data["running"])
        self.assertEqual(data["pid"], os.getpid())


# ------------------------------------------------------------------ D: sức khoẻ pin
class TestBattery(Base):
    def add_rows(self, n, days, start_mah):
        now = time.time()
        for i in range(n):
            ts = now - days * DAY + i * (days * DAY / max(1, n - 1))
            telemetry.append(self.ud, {"ts": ts, "battery_pct": 50, "status": "Discharging", "soc_c": 40.0, "gpu_c": 40.0,
                                       "batt_c": 30.0, "cpu_khz": 1000, "charge_full_uah": int((start_mah - i) * 1000),
                                       "health": "Good"})

    def test_01_no_samples_is_unavailable_not_invented(self):
        code, _, data = self.run_cli("battery")
        self.assertEqual(code, 0)
        self.assertFalse(data["available"])
        self.assertIsNone(data["estimated_pct"])
        self.assertTrue(data["reason"])

    def test_02_json_fields_from_real_samples(self):
        self.add_rows(10, 20, 3600)
        code, _, data = self.run_cli("battery")
        self.assertEqual(code, 0)
        self.assertTrue(data["available"])
        for k in ("charge_full_mah", "design_mah", "design_source", "estimated_pct", "driver_health", "trend_mah_per_30d", "samples"):
            self.assertIn(k, data)
        self.assertEqual(data["design_mah"], 4000)
        self.assertEqual(data["driver_health"], "Good")
        self.assertEqual(data["charge_full_mah"], 3591.0)           # mẫu cuối = 3600 - 9
        self.assertAlmostEqual(data["estimated_pct"], 89.8, places=1)
        self.assertLess(data["trend_mah_per_30d"], 0)

    def test_03_text_says_estimate_and_source(self):
        self.add_rows(4, 10, 3800)
        _, text, _ = self.run_cli("battery", json_out=False)
        self.assertIn("ƯỚC LƯỢNG", text)
        self.assertIn("nguồn:", text)
        self.assertIn("mAh/30 ngày", text)

    def test_04_ui_battery_items_label_estimate(self):
        header, items = ui.App._build_battery(telemetry.battery_health([]))
        self.assertEqual(items[0].label, "Chưa tính được")
        self.add_rows(10, 20, 3600)
        bh = telemetry.battery_health(telemetry.load(self.ud))
        header, items = ui.App._build_battery(bh)
        self.assertTrue(any("ƯỚC LƯỢNG" in h for h in header))
        by = {i.label: i for i in items}
        self.assertEqual(by["Dung lượng đầy hiện tại"].tag, "3591 mAh")
        self.assertEqual(by["Dung lượng thiết kế"].tag, "4000 mAh")
        self.assertIn("ước lượng", by["Sức khoẻ ước lượng"].tag)
        self.assertIn(bh["design_source"], by["Dung lượng thiết kế"].explain[0])


# ------------------------------------------------------------------ E: cập nhật
class UpdateBase(Base):
    def setUp(self):
        super().setUp()
        self.apps = self.tmp / "apps"
        for pak in ("LoongMate.pak", "LoongMateAgent.pak", "LoongMateNative.pak"):
            (self.apps / pak).mkdir(parents=True)
            (self.apps / pak / "launch.sh").write_bytes(b"OLD " + pak.encode())
        self.bundle = self.sd1 / ui.UPDATE_DIR_NAME / "v2"
        files = {"LoongMate.pak/launch.sh": b"NEW main", "LoongMate.pak/engine/cli.py": b"print('new')",
                 "LoongMateAgent.pak/launch.sh": b"NEW agent", "LoongMateNative.pak/launch.sh": b"NEW native"}
        for rel, data in files.items():
            p = self.bundle / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (self.bundle / "manifest.json").write_text(json.dumps(
            {"schema": 1, "version": "2.0.0", "created_at": "2026-10-02", "files": {k: sha(v) for k, v in files.items()}}), encoding="utf-8")

    def old(self):
        return (self.apps / "LoongMate.pak/launch.sh").read_bytes()


class TestUpdateCli(UpdateBase):
    def test_01_status_json(self):
        code, _, data = self.run_cli("update", "status", "--apps-dir", str(self.apps))
        self.assertEqual(code, 0)
        self.assertIsNone(data["installed"])
        self.assertEqual(data["backups"], [])

    def test_02_verify_ok_and_corrupt(self):
        code, _, data = self.run_cli("update", "verify", str(self.bundle))
        self.assertEqual((code, data["ok"], data["version"]), (0, True, "2.0.0"))
        (self.bundle / "LoongMate.pak/launch.sh").write_bytes(b"TAMPERED")
        code, _, data = self.run_cli("update", "verify", str(self.bundle))
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(any("sai sha256" in e for e in data["errors"]))

    def test_03_apply_refuses_without_yes_on_non_tty(self):
        code, _, data = self.run_cli("update", "apply", str(self.bundle), "--apps-dir", str(self.apps))
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertEqual(self.old(), b"OLD LoongMate.pak", "từ chối thì không được đổi gì")

    def test_04_apply_then_rollback(self):
        code, _, data = self.run_cli("update", "apply", str(self.bundle), "--yes", "--apps-dir", str(self.apps))
        self.assertEqual((code, data["ok"], data["version"]), (0, True, "2.0.0"))
        self.assertEqual(self.old(), b"NEW main")
        _, _, st = self.run_cli("update", "status", "--apps-dir", str(self.apps))
        self.assertEqual(st["installed"]["version"], "2.0.0")
        self.assertEqual(len(st["backups"]), 1)
        code, _, data = self.run_cli("update", "rollback", "--yes", "--apps-dir", str(self.apps))
        self.assertEqual((code, data["ok"]), (0, True))
        self.assertEqual(self.old(), b"OLD LoongMate.pak")

    def test_05_apply_corrupt_bundle_changes_nothing(self):
        (self.bundle / "LoongMateAgent.pak/launch.sh").write_bytes(b"TAMPERED")
        code, _, data = self.run_cli("update", "apply", str(self.bundle), "--yes", "--apps-dir", str(self.apps))
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertEqual(self.old(), b"OLD LoongMate.pak")

    def test_06_bundle_defaults_to_the_only_one_on_card(self):
        code, _, data = self.run_cli("update", "verify")
        self.assertEqual((code, data["ok"]), (0, True))


class TestUpdateScreen(UpdateBase):
    def make_app(self):
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        app = ui.App(probe, None, None, None, None, self.ud, None)
        patcher = mock.patch.dict(os.environ, {ui.APPS_DIR_ENV: str(self.apps)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    def test_01_confirm_defaults_to_cancel_then_apply_in_background(self):
        app = self.make_app()
        app.open_update()
        scr = app.cur()
        self.assertEqual(scr.cur_item().label, "v2")
        self.assertEqual(scr.cur_item().tag, "2.0.0")
        app.on_action("A")                       # kiểm tra gói (thread nền)
        app.wait_task()
        self.assertIsNotNone(app.dialog.on_confirm, "phải có hộp xác nhận")
        self.assertEqual(app.dialog.choice, 0, "mặc định là Huỷ")
        app.on_action("A")                       # bấm A khi đang ở Huỷ
        self.assertIsNone(app.dialog)
        self.assertEqual(self.old(), b"OLD LoongMate.pak", "Huỷ thì không đổi gì")
        app.on_action("A")
        app.wait_task()
        app.on_action("RIGHT")
        app.on_action("A")                       # xác nhận -> apply trong thread nền
        self.assertIsNotNone(app.task)
        app.wait_task()
        self.assertEqual(self.old(), b"NEW main")
        text = "\n".join(app.dialog.lines)
        self.assertIn("Đã cập nhật lên 2.0.0", text)
        self.assertIn(ui.RESTART_NOTE, text)

    def test_02_corrupt_bundle_shows_errors_no_confirm(self):
        (self.bundle / "LoongMate.pak/launch.sh").write_bytes(b"TAMPERED")
        app = self.make_app()
        app.open_update()
        app.on_action("A")
        app.wait_task()
        self.assertIsNone(app.dialog.on_confirm)
        self.assertEqual(app.dialog.title, "Gói KHÔNG hợp lệ")
        self.assertIn("sai sha256", "\n".join(app.dialog.lines))
        self.assertEqual(self.old(), b"OLD LoongMate.pak")

    def test_03_rollback_item_after_update(self):
        app = self.make_app()
        app.open_update()
        scr = app.cur()
        self.assertEqual([i.tag for i in scr.items if i.label == "Quay về bản trước"], ["không có"])
        from engine import update as update_mod
        self.assertTrue(update_mod.apply_update(self.bundle, self.apps, self.ud)["ok"])
        scr.refresh()
        item = [i for i in scr.items if i.label == "Quay về bản trước"][0]
        self.assertIsNotNone(item.a)
        scr.sel = scr.items.index(item)
        app.on_action("A")
        self.assertEqual(app.dialog.choice, 0)
        app.on_action("RIGHT")
        app.on_action("A")
        app.wait_task()
        self.assertEqual(self.old(), b"OLD LoongMate.pak")
        self.assertIn(ui.RESTART_NOTE_ROLLBACK, "\n".join(app.dialog.lines))


# ------------------------------------------------------------------ dedupe: dùng lại hash
class TestDedupeCacheNote(Base):
    def test_01_note_text(self):
        self.assertEqual(ui.hash_cache_note({"cache_hits": 12, "cache_misses": 3}),
                         " Dùng lại 12 hash đã lưu, băm mới 3 file.")
        self.assertEqual(ui.hash_cache_note({}), "")

    def test_02_cli_dedupe_uses_cache_and_reports_it(self):
        data = b"ROM" * 400
        (self.sd1 / "Roms/GBA").mkdir(parents=True)
        (self.sd2 / "Roms/GBA").mkdir(parents=True)
        (self.sd1 / "Roms/GBA/a.gba").write_bytes(data)
        (self.sd2 / "Roms/GBA/a.gba").write_bytes(data)
        code, _, rep = self.run_cli("dedupe")
        self.assertEqual(code, 0)
        self.assertEqual(len(rep["groups"]), 1)
        self.assertIn("cache_hits", rep)
        self.assertIn("cache_misses", rep)
        self.assertTrue((self.ud / "cache" / "hashes.sqlite").exists(), "CLI phải truyền HashCache cho dedupe")
        _, text, _ = self.run_cli("dedupe", json_out=False)
        self.assertIn("hash đã lưu", text)


if __name__ == "__main__":
    unittest.main()
