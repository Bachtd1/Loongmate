"""
Tests CLI đợt B (engine/cli.py): move, dedupe --copy-artwork, fix batch/undo, bios, history,
agent status, nodes, rescan và TỰ ĐỘNG RESCAN.

Mọi test gọi main([...]) thật trên thẻ giả trong thư mục tạm; stdin luôn là non-TTY.
Jawaka được thay bằng mock (engine.jawaka.rescan_available / request_rescan) để đếm số lần gọi.

Chứng minh test biết đỏ: đặt LOONGMATE_CLI_PATH tới một bản engine/cli.py cố tình làm hỏng
(vd. _auto_rescan luôn trả None, hoặc _gate luôn trả True); test sẽ chạy bản đó.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.config as config_mod
import engine.jawaka as jawaka
import engine.nodes as nodes_mod
import engine.telemetry as telemetry
import engine.transfer as transfer
from engine.autobackup import PidLock

_override = os.environ.get("LOONGMATE_CLI_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("cli_under_test", _override)
    CLI = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(CLI)
else:
    import engine.cli as CLI

MB = 1024 * 1024
GAME = b"GBA-ROM-" + bytes(range(256)) * 4
ART = b"\x89PNG-art-" + bytes(range(64))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_cliB_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir(parents=True)
        self.ud.mkdir()
        self.free = {"sd1": 900 * MB, "sd2": 25 * 1024 * MB}
        p = mock.patch.object(transfer, "_free_bytes", self._fake_free)
        p.start()
        self.addCleanup(p.stop)
        # Jawaka giả: sẵn sàng, đếm số lần request_rescan.
        self.available = {"available": True, "reason": ""}
        pa = mock.patch.object(jawaka, "rescan_available", lambda *a, **k: dict(self.available))
        self.req = mock.Mock(return_value={"ok": True, "message": "Quét xong: 5 game, 0 app.", "waited_s": 0.1})
        pr = mock.patch.object(jawaka, "request_rescan", self.req)
        pa.start()
        pr.start()
        self.addCleanup(pa.stop)
        self.addCleanup(pr.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_free(self, root):
        return self.free["sd1" if str(Path(root).resolve()).startswith(str(self.sd1.resolve())) else "sd2"]

    def w(self, path, data=GAME):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_cli(self, *argv, json_out=True, tty=False):
        """Trả (mã thoát, stdout, JSON đã parse hoặc None). stdin là non-TTY trừ khi tty=True."""
        base = ["--quiet", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud)]
        if json_out:
            base.append("--json")
        out, err = io.StringIO(), io.StringIO()
        stdin = io.StringIO("")
        if tty:
            stdin.isatty = lambda: True
            stdin = io.StringIO("n\n")
            stdin.isatty = lambda: True
        with mock.patch("sys.stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = CLI.main(base + list(argv))
        text = out.getvalue()
        data = None
        if json_out:
            # Phải là JSON hợp lệ, không lẫn chữ khác. Ngoại lệ duy nhất: câu hỏi xác nhận "[y/N]" của
            # input() khi có TTY (đó là lời nhắc tương tác chứ không phải kết quả).
            data = json.loads(text[text.index("{"):] if tty else text)
        return code, text, data


class TestMove(Base):
    def setUp(self):
        super().setUp()
        self.rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.art = self.w(self.sd1 / "Images/GBA/Game.png", ART)

    def test_01_plan_only_changes_nothing_and_json_valid(self):
        code, _, data = self.run_cli("move", str(self.rom), "--to", "secondary")
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["dest_card"], "secondary")
        self.assertTrue(self.rom.exists())
        self.assertFalse((self.sd2 / "Roms/GBA/Game.gba").exists())
        self.req.assert_not_called()

    def test_02_apply_noninteractive_without_yes_is_refused(self):
        code, _, data = self.run_cli("move", str(self.rom), "--to", "secondary", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertIn("--yes", data["message"])
        self.assertTrue(self.rom.exists())
        self.assertFalse((self.sd2 / "Roms/GBA/Game.gba").exists())
        self.req.assert_not_called()

    def test_03_apply_yes_really_moves_and_rescans_exactly_once(self):
        code, _, data = self.run_cli("move", str(self.rom), "--to", "secondary", "--apply", "--yes")
        self.assertEqual(code, 0, data)
        self.assertEqual((self.sd2 / "Roms/GBA/Game.gba").read_bytes(), GAME)
        self.assertEqual((self.sd2 / "Images/GBA/Game.png").read_bytes(), ART)
        self.assertFalse(self.rom.exists())
        self.assertFalse(self.art.exists())
        self.assertEqual(data["bytes_moved"], len(GAME) + len(ART))
        self.assertEqual(self.req.call_count, 1)
        self.assertTrue(data["rescan"]["attempted"] and data["rescan"]["ok"])

    def test_04_no_rescan_flag_skips_call_but_still_moves(self):
        code, _, data = self.run_cli("--no-rescan", "move", str(self.rom), "--to", "secondary", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue((self.sd2 / "Roms/GBA/Game.gba").exists())
        self.req.assert_not_called()
        self.assertFalse(data["rescan"]["attempted"])

    def test_05_failed_plan_returns_1_and_no_rescan(self):
        code, _, data = self.run_cli("move", str(self.rom), "--to", "primary", "--apply", "--yes")
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertTrue(self.rom.exists())
        self.req.assert_not_called()

    def test_06_bad_usage_returns_2(self):
        code, _, data = self.run_cli("move", "--to", "secondary")
        self.assertEqual(code, 2)
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary")
        self.assertEqual(code, 2)
        self.assertIn("target-free-mb", data["message"])

    def test_07_text_mode_noninteractive_refusal_message(self):
        code, text, _ = self.run_cli("move", str(self.rom), "--to", "secondary", "--apply", json_out=False)
        self.assertEqual(code, 1)
        self.assertIn("--yes", text)
        self.assertTrue(self.rom.exists())

    def test_08_tty_decline_moves_nothing(self):
        code, _, data = self.run_cli("move", str(self.rom), "--to", "secondary", "--apply", tty=True)
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertTrue(self.rom.exists())
        self.req.assert_not_called()


class TestMoveBulk(Base):
    def setUp(self):
        super().setUp()
        self.free["sd1"] = 100 * MB
        self.roms = [self.w(self.sd1 / f"Roms/GBA/G{i}.gba", b"x" * MB + bytes([i])) for i in range(3)]

    def test_01_bulk_plan_does_not_touch_files(self):
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary", "--target-free-mb", "101")
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertTrue(data["reached"])
        self.assertEqual(len(data["moves"]), 1)
        self.assertTrue(all(r.exists() for r in self.roms))
        self.req.assert_not_called()

    def test_02_bulk_apply_refused_without_yes_then_moves_with_yes(self):
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary", "--target-free-mb", "101", "--apply")
        self.assertEqual(code, 1)
        self.assertTrue(all(r.exists() for r in self.roms))
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary", "--target-free-mb", "101",
                                     "--apply", "--yes")
        self.assertEqual(code, 0, data)
        moved = [r for r in self.roms if not r.exists()]
        self.assertEqual(len(moved), 1)
        self.assertTrue((self.sd2 / "Roms/GBA" / moved[0].name).exists())
        self.assertEqual(self.req.call_count, 1)

    def test_03_target_already_met_means_no_move_and_no_rescan(self):
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary", "--target-free-mb", "50",
                                     "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(all(r.exists() for r in self.roms))
        self.req.assert_not_called()

    def test_04_system_filter_is_forwarded(self):
        self.w(self.sd1 / "Roms/GB/Z.gb", b"y" * MB)
        code, _, data = self.run_cli("move", "--bulk", "--to", "secondary", "--target-free-mb", "101",
                                     "--system", "GB")
        self.assertEqual(code, 0)
        self.assertEqual([m["system"] for m in data["moves"]], ["GB"])


class TestDedupeArtworkAndRescan(Base):
    def _dupes(self):
        self.a = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.b = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)

    def test_01_copy_artwork_flag_reaches_execute_deletion(self):
        self._dupes()
        code, _, data = self.run_cli("dedupe", "--apply", "--yes", "--prefer-card", "secondary", "--copy-artwork")
        self.assertEqual(code, 0, data)
        self.assertFalse(self.a.exists())
        self.assertTrue(self.b.exists())
        self.assertEqual((self.sd2 / "Images/GBA/Game.png").read_bytes(), ART)
        self.assertEqual(data["results"][0]["artwork"]["status"], "copied")

    def test_02_without_flag_no_artwork_copied(self):
        self._dupes()
        code, _, data = self.run_cli("dedupe", "--apply", "--yes", "--prefer-card", "secondary")
        self.assertEqual(code, 0)
        self.assertFalse((self.sd2 / "Images/GBA/Game.png").exists())

    def test_03_dedupe_apply_rescans_once(self):
        self._dupes()
        self.run_cli("dedupe", "--apply", "--yes")
        self.assertEqual(self.req.call_count, 1)

    def test_04_dedupe_noninteractive_refused_no_rescan(self):
        self._dupes()
        code, _, data = self.run_cli("dedupe", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertTrue(self.a.exists() and self.b.exists())
        self.req.assert_not_called()

    def test_05_dedupe_no_duplicates_no_rescan(self):
        self.w(self.sd1 / "Roms/GBA/A.gba", GAME)
        self.w(self.sd2 / "Roms/GBA/B.gba", GAME + b"different")
        code, _, _ = self.run_cli("dedupe", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.req.assert_not_called()

    def test_06_dedupe_no_rescan_flag(self):
        self._dupes()
        code, _, data = self.run_cli("--no-rescan", "dedupe", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(self.a.exists() and self.b.exists())  # vẫn xoá
        self.req.assert_not_called()

    def test_07_rom_delete_rescans_once_and_failure_does_not(self):
        rom = self.w(self.sd1 / "Roms/GBA/Del.gba")
        code, _, data = self.run_cli("rom", "delete", str(rom), "--yes")
        self.assertEqual(code, 0, data)
        self.assertFalse(rom.exists())
        self.assertEqual(self.req.call_count, 1)
        self.req.reset_mock()
        code, _, _ = self.run_cli("rom", "delete", str(self.sd1 / "Roms/GBA/khong-co.gba"), "--yes")
        self.assertEqual(code, 1)
        self.req.assert_not_called()

    def test_08_quarantine_purge_rescans_once_only_when_something_deleted(self):
        self._dupes()
        self.run_cli("--no-rescan", "dedupe", "--apply", "--yes", "--quarantine")
        self.req.reset_mock()
        code, _, data = self.run_cli("quarantine", "purge", "--yes")
        self.assertEqual(code, 0, data)
        self.assertGreaterEqual(len(data["deleted"]), 1)
        self.assertEqual(self.req.call_count, 1)
        self.req.reset_mock()
        code, _, _ = self.run_cli("quarantine", "purge", "--yes")  # rỗng: không thay đổi
        self.assertEqual(code, 0)
        self.req.assert_not_called()

    def test_09_unavailable_jawaka_falls_back_to_manual_reminder(self):
        self._dupes()
        self.available = {"available": False, "reason": "Jawaka chưa chạy"}
        code, text, data = self.run_cli("dedupe", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.req.assert_not_called()
        self.assertFalse(data["rescan"]["attempted"])
        self.assertIn("Rescan Library", data["rescan"]["message"])
        self.assertIn("Jawaka chưa chạy", data["rescan"]["message"])

    def test_10_text_mode_prints_rescan_result(self):
        self._dupes()
        code, text, _ = self.run_cli("dedupe", "--apply", "--yes", json_out=False)
        self.assertEqual(code, 0)
        self.assertIn("Quét xong: 5 game", text)

    def test_11_failed_rescan_does_not_change_exit_code(self):
        self._dupes()
        self.req.return_value = {"ok": False, "message": "Hết 60s", "waited_s": 60}
        code, _, data = self.run_cli("dedupe", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(data["rescan"]["ok"])


class TestFixBatch(Base):
    def setUp(self):
        super().setUp()
        self.src = self.w(self.sd1 / "Roms/PS/a.chd", b"AAA")
        self.dst = self.sd1 / "Roms/PSX/a.chd"

    def test_01_view_is_readonly_and_json_valid(self):
        code, _, data = self.run_cli("fix", "batch")
        self.assertEqual(code, 0)
        kinds = [s["kind"] for s in data["steps"]]
        self.assertIn("legacy_folder", kinds)
        self.assertTrue(self.src.exists())
        self.assertFalse((self.ud / "batchfix-journal.jsonl").exists())
        self.req.assert_not_called()

    def test_02_apply_noninteractive_without_yes_refused(self):
        code, _, data = self.run_cli("fix", "batch", "--apply")
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertTrue(self.src.exists() and not self.dst.exists())
        self.req.assert_not_called()

    def test_03_apply_yes_then_undo_roundtrip_with_rescan_each_once(self):
        code, _, data = self.run_cli("fix", "batch", "--apply", "--yes")
        self.assertEqual(code, 0, data)
        self.assertEqual(self.dst.read_bytes(), b"AAA")
        self.assertFalse(self.src.exists())
        self.assertEqual(self.req.call_count, 1)

        self.req.reset_mock()
        code, _, data = self.run_cli("fix", "undo")  # non-TTY, thiếu --yes
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertTrue(self.dst.exists())
        self.req.assert_not_called()

        code, _, data = self.run_cli("fix", "undo", "--yes")
        self.assertEqual(code, 0, data)
        self.assertEqual(self.src.read_bytes(), b"AAA")
        self.assertFalse(self.dst.exists())
        self.assertGreaterEqual(len(data["undone"]), 1)
        self.assertEqual(self.req.call_count, 1)

    def test_04_only_filter_limits_steps_and_nothing_to_do_means_no_rescan(self):
        code, _, data = self.run_cli("fix", "batch", "--only", "fix_cue")
        self.assertEqual([s["kind"] for s in data["steps"]], [])
        code, _, data = self.run_cli("fix", "batch", "--apply", "--yes", "--only", "fix_cue", "create_m3u")
        self.assertEqual(code, 0)
        self.assertTrue(self.src.exists())  # legacy_folder không nằm trong --only
        self.req.assert_not_called()

    def test_05_undo_with_nothing_to_undo_is_ok_and_no_rescan(self):
        code, _, data = self.run_cli("fix", "undo", "--yes")
        self.assertEqual(code, 0)
        self.assertIsNone(data["batch_id"])
        self.req.assert_not_called()

    def test_06_no_rescan_flag_on_apply_and_undo(self):
        self.run_cli("fix", "batch", "--apply", "--yes", "--no-rescan")
        self.assertTrue(self.dst.exists())
        self.run_cli("--no-rescan", "fix", "undo", "--yes")
        self.assertTrue(self.src.exists())
        self.req.assert_not_called()

    def test_07_empty_dest_dir_alias_only_noop_run_does_not_rescan(self):
        # Sau khi gộp xong, chạy lại: không còn gì để làm -> không rescan.
        self.run_cli("fix", "batch", "--apply", "--yes", "--no-rescan")
        code, _, data = self.run_cli("fix", "batch", "--apply", "--yes")
        self.assertEqual(code, 0)
        self.req.assert_not_called()


    def test_08_all_conflicts_means_nothing_changed_so_no_rescan(self):
        self.w(self.sd1 / "Roms/PS/g.chd", b"SRC-VERSION")
        self.w(self.sd1 / "Roms/PSX/g.chd", b"DEST-VERSION")
        self.src.unlink()
        code, _, data = self.run_cli("fix", "batch", "--apply", "--yes")
        self.assertEqual(code, 1)  # có bước bị từ chối
        self.assertEqual((self.sd1 / "Roms/PS/g.chd").read_bytes(), b"SRC-VERSION")
        self.assertIsNone(data["rescan"])
        self.req.assert_not_called()


class TestBios(Base):
    def test_01_json_table_and_text_marks_requirement(self):
        self.w(self.sd1 / "Roms/PSX/g.chd", b"G")
        self.w(self.sd1 / "BIOS/scph5501.bin", b"not-a-real-bios")
        code, _, data = self.run_cli("bios")
        self.assertEqual(code, 0)
        psx = next(s for s in data["systems"] if s["system"] == "PSX")
        st = {e["name"]: e["status"] for e in psx["required"]}
        self.assertEqual(st["scph5500.bin"], "missing")
        self.assertIn(st["scph5501.bin"], ("wrong_hash", "unknown_hash"))
        code, text, _ = self.run_cli("bios", json_out=False)
        self.assertEqual(code, 0)
        self.assertIn("scph5500.bin", text)
        self.assertIn("[THIẾU]", text)
        self.assertIn("bắt buộc", text)

    def test_02_no_roms_means_no_rows(self):
        code, _, data = self.run_cli("bios")
        self.assertEqual(data["systems"], [])


class TestHistoryAgent(Base):
    def _seed(self):
        now = int(time.time())
        t0 = now - 3600
        for i in range(12):  # 55 phút xả liên tục: 80% -> 69%
            telemetry.append(self.ud, {"ts": t0 + i * 300, "battery_pct": 80 - i, "status": "Discharging",
                                       "soc_c": 75.0 if i == 5 else 50.0, "gpu_c": 45.0, "batt_c": 30.0,
                                       "cpu_khz": 1000000})
        return now

    def test_01_summary_values(self):
        self._seed()
        code, _, data = self.run_cli("history", "--days", "7")
        self.assertEqual(code, 0)
        s = data["summary"]
        self.assertEqual(data["days"], 7)
        self.assertEqual(len(data["rows"]), 12)
        self.assertEqual(s["samples"], 12)
        self.assertEqual(s["drain_pct_per_hour"], 12.0)
        self.assertEqual(s["temp"]["soc"]["max"], 75.0)
        self.assertEqual(s["over_70_events"], 1)
        code, text, _ = self.run_cli("history", json_out=False)
        self.assertIn("12.0 %/giờ", text)
        self.assertIn("Vượt 70°C: 1 lần", text)

    def test_02_days_window_excludes_old_rows_and_empty_is_ok(self):
        old = int(time.time()) - 40 * 86400
        telemetry.append(self.ud, {"ts": old, "battery_pct": 50, "status": "Discharging",
                                   "soc_c": 40.0, "gpu_c": None, "batt_c": None, "cpu_khz": None})
        code, _, data = self.run_cli("history", "--days", "7")
        self.assertEqual(code, 0)
        self.assertEqual(data["rows"], [])
        code, _, data = self.run_cli("history", "--days", "60")
        self.assertEqual(len(data["rows"]), 1)

    def test_03_agent_status_not_running_then_running_when_lock_held(self):
        self._seed()
        code, _, data = self.run_cli("agent", "status")
        self.assertEqual(code, 0)
        self.assertFalse(data["running"])
        self.assertEqual(data["last_sample"]["battery_pct"], 69)
        self.assertIsNone(data["last_backup"])
        lock = PidLock(self.ud / "agent.lock")
        self.assertTrue(lock.acquire())
        try:
            code, _, data = self.run_cli("agent", "status")
            self.assertTrue(data["running"])
            self.assertEqual(data["pid"], os.getpid())
        finally:
            lock.release()

    def test_04_agent_status_reads_last_backup(self):
        (self.ud / "agent_state.json").write_text(json.dumps({"last_backup": "2026-10-01 20:00:00"}), encoding="utf-8")
        code, text, _ = self.run_cli("agent", "status", json_out=False)
        self.assertEqual(code, 0)
        self.assertIn("2026-10-01 20:00:00", text)


TS_STATUS = {
    "BackendState": "Running",
    "Self": {"HostName": "pocket1", "DNSName": "pocket1.tail.ts.net.", "TailscaleIPs": ["100.1.1.1"], "Online": True, "OS": "linux"},
    "Peer": {
        "a": {"HostName": "desktop", "DNSName": "desktop.tail.ts.net.", "TailscaleIPs": ["100.2.2.2"],
              "Online": True, "OS": "windows", "CurAddr": "1.2.3.4:41641"},
        "b": {"HostName": "nas", "DNSName": "nas.tail.ts.net.", "TailscaleIPs": ["100.3.3.3"],
              "Online": False, "OS": "linux", "LastSeen": "2026-10-01T00:00:00Z"},
    },
}


class TestNodes(Base):
    def _ts(self, rc=0, out=None, err=""):
        out = json.dumps(TS_STATUS) if out is None else out
        return mock.patch.object(nodes_mod, "_run_ts", lambda args, b, s, t: (rc, out, err))

    def test_01_list_merges_favorite_and_mac_from_config(self):
        self.run_cli("nodes", "fav", "nas", "on")
        self.run_cli("nodes", "mac", "desktop", "AA:BB:CC:DD:EE:FF")
        with self._ts():
            code, _, data = self.run_cli("nodes", "list")
        self.assertEqual(code, 0)
        by = {n["name"]: n for n in data["nodes"]}
        self.assertTrue(by["nas"]["favorite"])
        self.assertEqual(by["desktop"]["mac"], "aabbccddeeff")
        self.assertEqual(data["nodes"][0]["name"], "nas")  # yêu thích lên đầu
        with self._ts():
            code, text, _ = self.run_cli("nodes", "list", json_out=False)
        self.assertIn("desktop", text)

    def test_02_list_failure_returns_1(self):
        with self._ts(rc=None, out="", err="Không tìm thấy binary tailscale."):
            code, _, data = self.run_cli("nodes", "list")
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])

    def test_03_fav_off_and_invalid_name(self):
        self.run_cli("nodes", "fav", "nas", "on")
        self.assertIn("nas", config_mod.get_nodes_settings(config_mod.ConfigStore(self.ud))["favorites"])
        self.run_cli("nodes", "fav", "nas", "off")
        self.assertNotIn("nas", config_mod.get_nodes_settings(config_mod.ConfigStore(self.ud))["favorites"])
        code, _, data = self.run_cli("nodes", "fav", "bad name!", "on")
        self.assertEqual(code, 2)
        self.assertFalse(data["success"])

    def test_04_mac_invalid_returns_2_valid_normalised(self):
        code, _, data = self.run_cli("nodes", "mac", "desktop", "zz:zz")
        self.assertEqual(code, 2)
        code, _, _ = self.run_cli("nodes", "mac", "desktop", "AA-BB-CC-DD-EE-FF", "--broadcast", "192.168.1.255")
        self.assertEqual(code, 0)
        e = config_mod.get_nodes_settings(config_mod.ConfigStore(self.ud))["macs"]["desktop"]
        self.assertEqual(e, {"mac": "aabbccddeeff", "broadcast": "192.168.1.255"})

    def test_05_wake_needs_mac_then_sends_with_stored_broadcast(self):
        with mock.patch.object(nodes_mod, "wake_on_lan") as wol:
            code, _, data = self.run_cli("nodes", "wake", "desktop")
            self.assertEqual(code, 2)
            wol.assert_not_called()
            self.run_cli("nodes", "mac", "desktop", "AA:BB:CC:DD:EE:FF", "--broadcast", "192.168.1.255")
            wol.return_value = {"ok": True, "message": "đã gửi", "unverified": True}
            code, _, data = self.run_cli("nodes", "wake", "desktop")
            self.assertEqual(code, 0)
            wol.assert_called_once_with("aabbccddeeff", broadcast="192.168.1.255")

    def test_06_ping_parses_real_output(self):
        pong = "pong from desktop (100.2.2.2) via 1.2.3.4:41641 in 12ms\n"
        with self._ts(rc=1, out=pong):
            code, _, data = self.run_cli("nodes", "ping", "desktop")
        self.assertEqual(code, 0)
        self.assertEqual(data["latency_ms"], 12.0)
        self.assertEqual(data["via"], "direct")
        with self._ts(rc=1, out="no reply\n"):
            code, _, data = self.run_cli("nodes", "ping", "desktop")
        self.assertEqual(code, 1)

    def test_07_probe_uses_node_ip_and_custom_ports(self):
        states = {22: "open", 445: "closed"}
        with self._ts(), mock.patch.object(nodes_mod, "socks5_connect_state",
                                            lambda proxy, host, port, t: (states[port], "x")) as _:
            code, _, data = self.run_cli("nodes", "probe", "desktop", "--ports", "22", "445")
        self.assertEqual(code, 0)
        self.assertEqual(data["host"], "100.2.2.2")
        self.assertEqual([(p["port"], p["state"]) for p in data["ports"]], [(22, "open"), (445, "closed")])

    def test_08_probe_proxy_down_returns_1(self):
        with self._ts(), mock.patch.object(nodes_mod, "socks5_connect_state",
                                            lambda *a: ("proxy", "Không nối được SOCKS5")):
            code, _, data = self.run_cli("nodes", "probe", "desktop", "--ports", "22")
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])


class TestRescanCommand(Base):
    def test_01_calls_request_rescan_once_and_reports(self):
        code, _, data = self.run_cli("rescan")
        self.assertEqual(code, 0)
        self.assertEqual(self.req.call_count, 1)
        self.assertTrue(data["ok"])
        code, text, _ = self.run_cli("rescan", json_out=False)
        self.assertIn("Quét xong", text)

    def test_02_failure_exit_1(self):
        self.req.return_value = {"ok": False, "message": "Không gọi được jawakad", "waited_s": 0.0}
        code, _, data = self.run_cli("rescan")
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
