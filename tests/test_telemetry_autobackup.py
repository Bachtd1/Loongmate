"""
Test hanh vi that cho engine/telemetry.py va engine/autobackup.py (+ agent).
Khong grep ma nguon: moi test chay code that tren thu muc tam / tien trinh that.
Chay: python -m unittest discover -s tests   (tu thu muc goc du an)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from engine import telemetry
from engine.autobackup import (AutoBackup, Event, LogWatcher, PidLock, ProcWatcher, parse_event)
from engine.savevault import SaveVault

ROOT = Path(__file__).resolve().parent.parent

# Dong log THAT lay tu umrk-leaf-session.log tren Pocket 1 (2026-09-28).
REAL_START = ("2026-09-28 15:30:19 INFO life1: active launch committed "
              "launch_id=1790605819-907826206-1000-1-242238981 source=primary kind=RETROARCH")
REAL_FINISH = ("2026-09-28 15:33:48 INFO life1: game.finish "
               "launch_id=1790605819-907826206-1000-1-242238981 after writer barrier")
REAL_NOISE = "2026-09-28 15:33:47 INFO recorded play game_id=3069 duration_s=206"


class FakeProbe:
    def __init__(self, available=True, percent=80, status="Discharging"):
        self.info = {"available": available, "percent": percent if available else None,
                     "status": status, "is_charging": status.lower() in ("charging", "full")}

    def get_battery_info(self):
        return self.info


def make_thermal(root: Path, zones):
    for i, (ztype, temp) in enumerate(zones):
        z = root / f"thermal_zone{i}"
        z.mkdir(parents=True)
        (z / "type").write_text(ztype + "\n")
        if temp is not None:
            (z / "temp").write_text(f"{temp}\n")


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_tel_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestTelemetry(TmpCase):
    def test_sample_reads_zones_by_type_and_cpufreq(self):
        make_thermal(self.tmp / "th", [("soc-thermal", 45555), ("gpu-thermal", 43750), ("battery", 18800)])
        (self.tmp / "freq").write_text("1416000\n")
        # power_root trỏ tới chỗ không tồn tại để test không phụ thuộc /sys thật của máy chạy test.
        row = telemetry.sample(FakeProbe(percent=77), self.tmp / "th", self.tmp / "freq", now=1000,
                               power_root=self.tmp / "no-power")
        self.assertEqual(row, {"ts": 1000, "battery_pct": 77, "status": "Discharging",
                               "soc_c": 45.6, "gpu_c": 43.8, "batt_c": 18.8, "cpu_khz": 1416000,
                               "charge_full_uah": None, "current_ua": None, "voltage_uv": None, "health": None})

    def test_sample_missing_sensors_left_blank_not_filled(self):
        # khong co thermal, khong co cpufreq, pin khong doc duoc
        row = telemetry.sample(FakeProbe(available=False), self.tmp / "nothing", self.tmp / "nofreq", now=5,
                               power_root=self.tmp / "no-power")
        self.assertEqual(row, {"ts": 5, "battery_pct": None, "status": None,
                               "soc_c": None, "gpu_c": None, "batt_c": None, "cpu_khz": None,
                               "charge_full_uah": None, "current_ua": None, "voltage_uv": None, "health": None})
        # chi thieu 1 vung: vung do None, vung khac van doc
        make_thermal(self.tmp / "th", [("soc-thermal", 50000), ("gpu-thermal", None)])
        row = telemetry.sample(FakeProbe(), self.tmp / "th", self.tmp / "nofreq", now=6)
        self.assertEqual(row["soc_c"], 50.0)
        self.assertIsNone(row["gpu_c"])
        self.assertIsNone(row["batt_c"])

    def test_blank_survives_csv_roundtrip_as_none_not_zero(self):
        row = telemetry.sample(FakeProbe(available=False), self.tmp / "x", self.tmp / "y", now=time.time())
        telemetry.append(self.tmp, row)
        loaded = telemetry.load(self.tmp)
        self.assertEqual(len(loaded), 1)
        for k in ("battery_pct", "soc_c", "gpu_c", "batt_c", "cpu_khz", "status"):
            self.assertIsNone(loaded[0][k], k)

    def test_append_load_header_once_month_file_since_and_bad_line(self):
        t0 = time.mktime((2026, 9, 30, 23, 55, 0, 0, 0, -1))
        t1 = time.mktime((2026, 10, 1, 0, 5, 0, 0, 0, -1))
        telemetry.append(self.tmp, {"ts": int(t0), "battery_pct": 50, "status": "Discharging", "soc_c": 40.0,
                                    "gpu_c": 41.0, "batt_c": 20.0, "cpu_khz": 1000})
        telemetry.append(self.tmp, {"ts": int(t1), "battery_pct": 49, "status": "Discharging", "soc_c": 41.5,
                                    "gpu_c": None, "batt_c": None, "cpu_khz": None})
        files = sorted(p.name for p in (self.tmp / "telemetry").iterdir())
        self.assertEqual(files, ["2026-09.csv", "2026-10.csv"])
        text = (self.tmp / "telemetry" / "2026-09.csv").read_text()
        self.assertEqual(text.count("ts,battery_pct"), 1)
        # dong cuoi bi cat do mat dien + dong rac
        with open(self.tmp / "telemetry" / "2026-10.csv", "a") as f:
            f.write("garbage\n1759300000,4")
        rows = telemetry.load(self.tmp)
        self.assertEqual([r["ts"] for r in rows], [int(t0), int(t1)])
        self.assertEqual(rows[1]["soc_c"], 41.5)
        self.assertEqual([r["ts"] for r in telemetry.load(self.tmp, since_ts=int(t1))], [int(t1)])

    def test_summarize_drain_rate_ignores_charging_and_gaps(self):
        rows = []
        t = 1_000_000
        # 1 gio xa pin 5 phut/mau: 80 -> 72 (8 %/gio)
        for i in range(13):
            rows.append({"ts": t + i * 300, "battery_pct": 80 - round(i * 8 / 12), "status": "Discharging",
                         "soc_c": 40.0, "gpu_c": None, "batt_c": None, "cpu_khz": None})
        t_end = rows[-1]["ts"]
        # khoang trong 2 gio (may tat) roi tut con 50: KHONG duoc tinh
        rows.append({"ts": t_end + 7200, "battery_pct": 50, "status": "Discharging",
                     "soc_c": 40.0, "gpu_c": None, "batt_c": None, "cpu_khz": None})
        # sac: pin tang, khong duoc tinh
        for i in range(1, 6):
            rows.append({"ts": t_end + 7200 + i * 300, "battery_pct": 50 + i * 5, "status": "Charging",
                         "soc_c": 40.0, "gpu_c": None, "batt_c": None, "cpu_khz": None})
        s = telemetry.summarize(rows)
        self.assertEqual(s["drain_pct_per_hour"], 8.0)
        self.assertEqual(s["discharge_hours"], 1.0)

    def test_summarize_not_enough_data_is_none_and_temps(self):
        rows = [{"ts": 0, "battery_pct": 50, "status": "Discharging", "soc_c": 60.0, "gpu_c": None,
                 "batt_c": None, "cpu_khz": None},
                {"ts": 300, "battery_pct": 50, "status": "Discharging", "soc_c": 71.0, "gpu_c": 40.0,
                 "batt_c": None, "cpu_khz": None}]
        s = telemetry.summarize(rows)
        self.assertIsNone(s["drain_pct_per_hour"])
        self.assertEqual(s["temp"]["soc"], {"max": 71.0, "avg": 65.5})
        self.assertEqual(s["temp"]["gpu"], {"max": 40.0, "avg": 40.0})
        self.assertIsNone(s["temp"]["batt"])
        self.assertEqual(telemetry.summarize([])["samples"], 0)

    def test_summarize_over_70_counts_episodes_not_samples(self):
        temps = [60, 71, 72, 60, 75, 80, 50]
        rows = [{"ts": i * 300, "battery_pct": 50, "status": "Discharging", "soc_c": float(v),
                 "gpu_c": None, "batt_c": None, "cpu_khz": None} for i, v in enumerate(temps)]
        s = telemetry.summarize(rows)
        self.assertEqual(s["over_70_events"], 2)
        self.assertEqual(s["over_70_samples"], 4)

    def test_prune_removes_only_old_months(self):
        now = time.mktime((2026, 10, 2, 12, 0, 0, 0, 0, -1))
        for ym in [(2026, 4), (2026, 6), (2026, 7), (2026, 10)]:
            ts = int(time.mktime((ym[0], ym[1], 15, 12, 0, 0, 0, 0, -1)))
            telemetry.append(self.tmp, {"ts": ts, "battery_pct": 1, "status": "x", "soc_c": 1.0,
                                        "gpu_c": None, "batt_c": None, "cpu_khz": None})
        (self.tmp / "telemetry" / "notes.txt").write_text("keep")
        removed = telemetry.prune(self.tmp, keep_days=90, now=now)
        self.assertEqual(sorted(removed), ["2026-04.csv", "2026-06.csv"])
        left = sorted(p.name for p in (self.tmp / "telemetry").iterdir())
        self.assertEqual(left, ["2026-07.csv", "2026-10.csv", "notes.txt"])


class FakeVault:
    def __init__(self, results=None):
        self.calls = []
        self.results = list(results or [])

    def create_snapshot(self, destination=None, label="manual"):
        self.calls.append(label)
        if self.results:
            return self.results.pop(0)
        return {"success": True, "snapshot_id": f"snap_{len(self.calls)}_auto", "files_count": 3}

    def list_snapshots(self):
        return [{"id": f"snap_{i}_auto"} for i in range(len(self.calls))]


FIN = Event("finish", "L1", "log")
START = Event("start", "L2", "log")


class TestDetection(TmpCase):
    def test_parse_event_on_real_lines(self):
        self.assertEqual(parse_event(REAL_START), ("start", "1790605819-907826206-1000-1-242238981"))
        self.assertEqual(parse_event(REAL_FINISH), ("finish", "1790605819-907826206-1000-1-242238981"))
        self.assertIsNone(parse_event(REAL_NOISE))
        self.assertIsNone(parse_event("2026-10-02 12:14:41 INFO frontend-ready role=launcher code=ok"))

    def test_logwatcher_ignores_history_and_handles_partial_lines(self):
        log = self.tmp / "leaf.log"
        log.write_text(REAL_START + "\n" + REAL_FINISH + "\n")        # lich su cu
        w = LogWatcher(log)
        self.assertEqual(w.poll(), [])
        with open(log, "a") as f:
            f.write(REAL_START.replace("1790605819", "1790999999") + "\n" + REAL_NOISE + "\n")
            f.write(REAL_FINISH.replace("1790605819", "1790999999")[:40])   # dong do
        evs = w.poll()
        self.assertEqual([e.kind for e in evs], ["start"])
        with open(log, "a") as f:
            f.write(REAL_FINISH.replace("1790605819", "1790999999")[40:] + "\n")
        evs = w.poll()
        self.assertEqual([(e.kind, e.launch_id) for e in evs], [("finish", "1790999999-907826206-1000-1-242238981")])
        # cung launch_id bi ghi lai lan nua -> khong phat lai
        with open(log, "a") as f:
            f.write(REAL_FINISH.replace("1790605819", "1790999999") + "\n")
        self.assertEqual(w.poll(), [])

    def test_logwatcher_survives_truncation_and_late_creation(self):
        log = self.tmp / "leaf.log"
        w = LogWatcher(log)                    # file chua ton tai
        self.assertFalse(w.exists())
        self.assertEqual(w.poll(), [])
        log.write_text(REAL_START + "\n")      # file xuat hien
        self.assertEqual([e.kind for e in w.poll()], ["start"])
        log.write_text(REAL_FINISH + "\n")     # bi cat/xoay vong, ngan hon offset
        self.assertEqual([e.kind for e in w.poll()], ["finish"])

    def test_procwatcher_start_and_finish_on_fake_proc(self):
        proc = self.tmp / "proc"
        (proc / "self").mkdir(parents=True)
        for pid, comm in (("100", "bash"), ("200", "retroarch")):
            (proc / pid).mkdir()
            (proc / pid / "comm").write_text(comm + "\n")
        w = ProcWatcher(proc)
        self.assertEqual([e.kind for e in w.poll()], ["start"])
        self.assertEqual(w.poll(), [])                      # van chay: khong phat lai
        shutil.rmtree(proc / "200")
        evs = w.poll()
        self.assertEqual([(e.kind, e.source) for e in evs], [("finish", "proc")])
        self.assertEqual(w.poll(), [])


class TestAutoBackup(TmpCase):
    def make(self, vault=None, **kw):
        logs = []
        ab = AutoBackup(vault or FakeVault(), log=logs.append, **kw)
        return ab, logs

    def test_one_finish_event_gives_exactly_one_backup_after_settle(self):
        v = FakeVault()
        ab, _ = self.make(v, min_interval=600, settle_s=10)
        ab.feed([FIN, FIN], now=100)             # trung su kien cung luc
        self.assertIsNone(ab.tick(105))          # chua het 10 giay
        self.assertEqual(v.calls, [])
        res = ab.tick(110)
        self.assertTrue(res["success"])
        self.assertEqual(v.calls, ["auto"])
        self.assertIsNone(ab.tick(120))
        self.assertIsNone(ab.tick(5000))
        self.assertEqual(v.calls, ["auto"])

    def test_debounce_blocks_second_then_defers_not_drops(self):
        v = FakeVault()
        ab, _ = self.make(v, min_interval=600, settle_s=10)
        ab.feed([FIN], now=0)
        ab.tick(10)
        ab.feed([FIN], now=100)                  # game thu hai thoat trong 10 phut
        ab.tick(110)
        ab.tick(500)
        self.assertEqual(len(v.calls), 1)        # bi chan
        ab.tick(609)
        self.assertEqual(len(v.calls), 1)
        ab.tick(611)                             # het debounce: ban hoan lai chay
        self.assertEqual(len(v.calls), 2)

    def test_no_backup_while_a_game_is_running(self):
        v = FakeVault()
        ab, _ = self.make(v, min_interval=0, settle_s=10)
        ab.feed([FIN], now=0)
        ab.feed([START], now=5)                  # vao game khac truoc khi het settle
        self.assertIsNone(ab.tick(20))
        self.assertEqual(v.calls, [])
        ab.feed([FIN], now=300)
        ab.tick(310)
        self.assertEqual(len(v.calls), 1)

    def test_stuck_active_flag_expires(self):
        from engine.autobackup import GAME_ACTIVE_MAX_S
        v = FakeVault()
        ab, _ = self.make(v, min_interval=0, settle_s=10)
        ab.feed([FIN], now=0)
        ab.feed([START], now=5)                   # "start" ma finish tuong ung khong bao gio den
        self.assertIsNone(ab.tick(GAME_ACTIVE_MAX_S - 100))
        self.assertIsNotNone(ab.tick(5 + GAME_ACTIVE_MAX_S + 1))
        self.assertEqual(len(v.calls), 1)

    def test_disk_low_warns_once_until_success(self):
        low = {"success": False, "error": "DISK_LOW_SPACE", "message": "het cho"}
        ok = {"success": True, "snapshot_id": "snap_x_auto", "files_count": 1}
        v = FakeVault([low, low, low, ok, low])
        ab, logs = self.make(v, min_interval=100, settle_s=1)
        t = 0
        for _ in range(3):
            ab.feed([FIN], now=t)
            ab.tick(t + 1)
            t += 200
        warns = [m for m in logs if "CANH BAO" in m]
        self.assertEqual(len(warns), 1)
        self.assertEqual(len(v.calls), 3)
        ab.feed([FIN], now=t); ab.tick(t + 1); t += 200      # thanh cong -> reset
        ab.feed([FIN], now=t); ab.tick(t + 1)                # lai day
        self.assertEqual(len([m for m in logs if "CANH BAO" in m]), 2)

    def test_real_savevault_makes_auto_snapshot_without_pruning_manual(self):
        primary = self.tmp / "sd1"
        (primary / "Saves").mkdir(parents=True)
        (primary / "Saves" / "a.srm").write_bytes(b"save-1")
        ud = self.tmp / "ud"
        vault = SaveVault(primary, None, ud, max_retention=10 ** 6)
        manual = vault.create_snapshot(label="manual")
        self.assertTrue(manual["success"], manual)
        ab, _ = self.make(vault, min_interval=0, settle_s=0)
        for i in range(8):                                    # nhieu hon retention mac dinh 5
            (primary / "Saves" / "a.srm").write_bytes(f"save-{i}".encode())
            time.sleep(1.05)                                  # id snapshot do phan giai 1 giay
            ab.feed([FIN], now=1000 + i)
            self.assertTrue(ab.tick(1000 + i)["success"])
        ids = [s["id"] for s in vault.list_snapshots()]
        self.assertEqual(len([i for i in ids if "_auto" in i]), 8)
        self.assertIn(manual["snapshot_id"], ids)


class TestPidLock(TmpCase):
    def test_second_lock_refused_then_allowed_after_release(self):
        a, b = PidLock(self.tmp / "agent.lock"), PidLock(self.tmp / "agent.lock")
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        self.assertEqual(b.holder_pid(), os.getpid())
        a.release()
        self.assertTrue(b.acquire())
        b.release()

    def test_second_process_is_refused_while_first_holds_lock(self):
        lock = PidLock(self.tmp / "agent.lock")
        self.assertTrue(lock.acquire())
        try:
            code = ("import sys; sys.path.insert(0, %r); from engine.autobackup import PidLock; "
                    "sys.exit(0 if PidLock(%r).acquire() else 7)") % (str(ROOT), str(self.tmp / "agent.lock"))
            r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 7, r.stderr)
        finally:
            lock.release()
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_agent_refuses_to_start_when_lock_is_held(self):
        lock = PidLock(self.tmp / "ud" / "agent.lock")
        self.assertTrue(lock.acquire())
        try:
            r = subprocess.run([sys.executable, "-m", "engine.agent", "--run", "--userdata", str(self.tmp / "ud"),
                                "--primary", str(self.tmp), "--duration", "2"],
                               cwd=str(ROOT), capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
            self.assertFalse((self.tmp / "ud" / "telemetry").exists())   # khong chay vong lap
        finally:
            lock.release()


class TestAgentRun(TmpCase):
    def test_agent_run_writes_telemetry_and_backs_up_on_finish_line(self):
        primary = self.tmp / "sd1"
        (primary / "Saves").mkdir(parents=True)
        (primary / "Saves" / "a.srm").write_bytes(b"x")
        leaf_log = self.tmp / "leaf.log"
        leaf_log.write_text(REAL_START + "\n")
        ud = self.tmp / "ud"
        p = subprocess.Popen([sys.executable, "-m", "engine.agent", "--run", "--userdata", str(ud),
                              "--primary", str(primary), "--leaf-log", str(leaf_log), "--duration", "12",
                              "--interval", "1", "--poll", "0.5", "--settle", "1"],
                             cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            time.sleep(3)
            with open(leaf_log, "a") as f:
                f.write(REAL_FINISH + "\n")
            out, _ = p.communicate(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, out)
        self.assertGreaterEqual(len(telemetry.load(ud)), 2, out)
        snaps = [s["id"] for s in SaveVault(primary, None, ud).list_snapshots()]
        self.assertEqual(len([s for s in snaps if s.endswith("_auto")]), 1, out)


if __name__ == "__main__":
    unittest.main()
