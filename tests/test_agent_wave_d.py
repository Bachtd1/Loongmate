"""
Dot D (agent nen): tu nap lai sau cap nhat, kiem tra toan ven SaveVault, bam truoc ROM khi sac, don goi cap nhat cu.
Moi test chay code that tren thu muc tam voi dong ho / su kien / pin GIA (khong cho that, khong cham may that).
Chay: python -m unittest discover -s tests   (tu thu muc goc du an)
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from engine import agent, update as up
from engine.hashcache import HashCache
from engine.inventory import InventoryEngine

CHUNK_DELAY_S = 0.1    # do tre moi lan doc chunk 256 KB (~2.5 MB/s): file 8 MB mat > 3 s neu khong ngat giua file


class _SlowFile:
    def __init__(self, f):
        self.f = f

    def read(self, n=-1):
        time.sleep(CHUNK_DELAY_S)
        return self.f.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.f.close()


def slow_open(path, mode="r", *a, **k):
    """Thay open() trong engine.agent: moi file > 2 s neu bam het ma khong ngat GIUA file."""
    return _SlowFile(open(path, mode, *a, **k))

CATALOG = {"GBA": {"folder": "GBA", "extensions": [".gba"]}}


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Env:
    """Dong ho gia: sleep() chi cong don thoi gian, khong cho."""

    def __init__(self):
        self.mono, self.wall_t = 1000.0, 1_800_000_000.0
        self.game = False
        self.battery = {"available": True, "percent": 80, "status": "Discharging"}
        self.pending = False
        self.logs = []
        self.game_calls = 0

    def clock(self):
        return self.mono

    def wall(self):
        return self.wall_t

    def sleep(self, s):
        self.advance(s)

    def advance(self, s):
        self.mono += s
        self.wall_t += s

    def game_busy(self):
        self.game_calls += 1
        return self.game


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "userdata" / "LoongMate"
        self.data.mkdir(parents=True)
        self.env = Env()

    def maint(self, **kw):
        e = self.env
        args = dict(game_busy=e.game_busy, battery=lambda: e.battery, log=e.logs.append, clock=e.clock, wall=e.wall,
                    sleep=e.sleep, backup_pending=lambda: e.pending, stop_check_s=0)
        args.update(kw)
        return agent.Maintenance(self.data, **args)

    def state(self):
        return json.loads((self.data / agent.STATE_NAME).read_text(encoding="utf-8"))

    def tick(self, m):
        self.env.advance(agent.MAINT_EVERY_S + 1)
        return m.tick()


# --------------------------------------------------------------------------- 1. tu nap lai

class TestReload(Base):
    def set_version(self, v, age=100):
        p = self.data / agent.VERSION_NAME
        p.write_text(json.dumps({"version": v}), encoding="utf-8")
        t = self.env.wall() - age
        os.utime(p, (t, t))

    def test_reload_when_version_changes(self):
        self.set_version("v1")
        m = self.maint()
        self.assertEqual(m.start_version, "v1")
        self.assertIsNone(self.tick(m))                 # chua doi
        self.set_version("v2")
        self.assertEqual(self.tick(m), "reload")
        r = self.state()["reload"]
        self.assertEqual((r["from"], r["to"], r["ts"]), ("v1", "v2", int(self.env.wall())))
        self.assertTrue(any("75" in l for l in self.env.logs), self.env.logs)

    def test_reload_when_start_version_none(self):
        m = self.maint()
        self.assertIsNone(m.start_version)
        self.set_version("v1")
        self.assertEqual(self.tick(m), "reload")
        self.assertIsNone(self.state()["reload"]["from"])

    def test_no_reload_while_game_then_reload_after(self):
        self.set_version("v1")
        m = self.maint()
        self.set_version("v2")
        self.env.game = True
        self.assertIsNone(self.tick(m))
        self.assertNotIn("reload", self.state() if (self.data / agent.STATE_NAME).exists() else {})
        self.env.game = False
        self.assertEqual(self.tick(m), "reload")

    def test_no_reload_while_backup_pending(self):
        self.set_version("v1")
        m = self.maint()
        self.set_version("v2")
        self.env.pending = True
        self.assertIsNone(self.tick(m))
        self.env.pending = False
        self.assertEqual(self.tick(m), "reload")

    def test_no_reload_until_file_stable_10s(self):
        self.set_version("v1")
        m = self.maint()
        self.set_version("v2", age=3)
        self.assertIsNone(m.tick())                     # file moi ghi 3 s truoc
        m._next_tick = None
        self.set_version("v2", age=11)
        self.assertEqual(m.tick(), "reload")

    def test_loop_guard_blocks_second_reload_to_same_version(self):
        self.set_version("v1")
        m = self.maint()
        self.set_version("v2")
        ts0 = int(self.env.wall()) - 100
        agent._write_state(self.data, {"reload": {"from": "v1", "to": "v2", "ts": ts0}})
        self.assertIsNone(self.tick(m))
        self.assertTrue(any("CANH BAO" in l for l in self.env.logs), self.env.logs)
        self.assertEqual(self.state()["reload"]["ts"], ts0)              # khong bi ghi de
        self.env.advance(agent.RELOAD_GUARD_S)                           # qua 10 phut thi het chan
        self.assertEqual(self.tick(m), "reload")

    def test_loop_guard_does_not_block_other_version(self):
        self.set_version("v1")
        m = self.maint()
        self.set_version("v3")
        agent._write_state(self.data, {"reload": {"from": "v1", "to": "v2", "ts": int(self.env.wall()) - 100}})
        self.assertEqual(self.tick(m), "reload")


class TestRunExitCode(Base):
    def test_cmd_run_exits_75_and_releases_lock_when_version_changes(self):
        vp = self.data / agent.VERSION_NAME
        vp.write_text(json.dumps({"version": "v1"}), encoding="utf-8")
        primary = self.root / "sd"
        primary.mkdir()

        def bump():
            time.sleep(1.0)
            vp.write_text(json.dumps({"version": "v2"}), encoding="utf-8")
        threading.Thread(target=bump, daemon=True).start()
        argv = ["--run", "--userdata", str(self.data), "--primary", str(primary), "--poll", "0.5",
                "--leaf-log", str(self.root / "khong-co.log"), "--duration", "20"]
        with mock.patch.object(agent, "MAINT_EVERY_S", 0), mock.patch.object(agent, "VERSION_STABLE_S", 0):
            t0 = time.monotonic()
            rc = agent.main(argv)
        self.assertEqual(rc, 75)
        self.assertLess(time.monotonic() - t0, 15)
        self.assertEqual((self.state()["reload"]["from"], self.state()["reload"]["to"]), ("v1", "v2"))
        self.assertIs(agent.lock_held(self.data), False)         # khoa da nha


# --------------------------------------------------------------------------- 2. verify SaveVault

def make_vault(root, objs, committed=True):
    """objs: {ten_save: bytes}. Tra ve {ten_save: sha}. Layout 'objects' nhu SaveVault."""
    vault = Path(root) / "SaveVault"
    files = {}
    for name, data in objs.items():
        h = sha(data)
        p = vault / "objects" / h[:2] / h
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files[name] = {"sha256": h, "size": len(data)}
    snap = vault / "snapshots" / "snap_20260101_000000_auto"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "manifest.json").write_text(json.dumps(
        {"snapshot_id": snap.name, "committed": committed, "layout": "objects", "files": files}), encoding="utf-8")
    return vault, {n: i["sha256"] for n, i in files.items()}


class TestVerify(Base):
    def run_verify(self, vault, **kw):
        m = self.maint(vault_dir=vault, **kw)
        self.tick(m)
        return m

    def test_clean_vault_ok(self):
        vault, _ = make_vault(self.root, {f"s{i}.sav": os.urandom(100) for i in range(6)})
        self.run_verify(vault)
        v = self.state()["vault_verify"]
        self.assertEqual((v["ok"], v["checked"], v["bad_objects"], v["missing_objects"], v["partial"]),
                         (True, 6, [], 0, False))
        self.assertIsInstance(v["ts"], int)

    def test_detects_flipped_byte_and_missing_object(self):
        vault, ids = make_vault(self.root, {f"s{i}.sav": os.urandom(100) for i in range(6)})
        bad = vault / "objects" / ids["s1.sav"][:2] / ids["s1.sav"]
        data = bytearray(bad.read_bytes())
        data[10] ^= 0x01
        bad.write_bytes(bytes(data))
        gone = vault / "objects" / ids["s4.sav"][:2] / ids["s4.sav"]
        gone.unlink()
        before = {p: p.read_bytes() for p in (vault / "objects").rglob("*") if p.is_file()}
        self.run_verify(vault)
        v = self.state()["vault_verify"]
        self.assertFalse(v["ok"])
        self.assertEqual(v["bad_objects"], [ids["s1.sav"]])
        self.assertEqual(v["missing_objects"], 1)
        self.assertEqual(v["checked"], 5)
        # chi doc: khong sua/xoa gi
        after = {p: p.read_bytes() for p in (vault / "objects").rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_bad_objects_capped_at_20(self):
        vault, ids = make_vault(self.root, {f"s{i}.sav": os.urandom(50) for i in range(30)})
        for h in ids.values():
            p = vault / "objects" / h[:2] / h
            p.write_bytes(p.read_bytes() + b"x")
        self.run_verify(vault)
        v = self.state()["vault_verify"]
        self.assertEqual(len(v["bad_objects"]), 20)
        self.assertFalse(v["ok"])

    def test_not_due_within_7_days_due_after(self):
        vault, _ = make_vault(self.root, {"a.sav": b"aaa"})
        m = self.run_verify(vault)
        ts1 = self.state()["vault_verify"]["ts"]
        self.env.advance(6 * 86400)
        self.tick(m)
        self.assertEqual(self.state()["vault_verify"]["ts"], ts1)
        self.env.advance(2 * 86400)
        self.tick(m)
        self.assertGreater(self.state()["vault_verify"]["ts"], ts1)

    def test_skipped_when_game_or_low_battery_not_charging(self):
        vault, _ = make_vault(self.root, {"a.sav": b"aaa"})
        m = self.maint(vault_dir=vault)
        self.env.game = True
        self.tick(m)
        self.env.game = False
        self.env.battery = {"available": True, "percent": 49, "status": "Discharging"}
        self.tick(m)
        self.assertFalse((self.data / agent.STATE_NAME).exists())
        self.env.battery = {"available": True, "percent": 20, "status": "Charging"}
        self.tick(m)
        self.assertTrue(self.state()["vault_verify"]["ok"])

    def test_stops_when_game_starts_and_resumes_next_time(self):
        vault, _ = make_vault(self.root, {f"s{i}.sav": os.urandom(100) for i in range(10)})
        e = self.env
        calls = {"n": 0}

        def busy():
            calls["n"] += 1
            return calls["n"] > 12            # vai doi tick + vai object dau roi game bat dau

        m = self.maint(vault_dir=vault, game_busy=busy)
        self.tick(m)
        v = self.state()["vault_verify"]
        self.assertTrue(v["partial"], v)
        self.assertTrue(0 < v["checked"] < 10, v)
        k = v["checked"]
        self.assertIsNotNone(self.state()["vault_verify_cursor"])
        # lan sau (het game): tiep tuc, khong bam lai phan da xong
        calls["n"] = -10 ** 6
        real = agent.hash_file_interruptible
        hashed = []

        def spy(path, stop, chunk=1 << 18):
            hashed.append(Path(path).name)
            return real(path, stop, chunk)
        with mock.patch.object(agent, "hash_file_interruptible", spy):
            self.tick(m)
        v2 = self.state()["vault_verify"]
        self.assertFalse(v2["partial"], v2)
        self.assertEqual(v2["checked"], 10)
        self.assertEqual(len(hashed), 10 - k)
        self.assertTrue(v2["ok"])
        self.assertIsNone(self.state()["vault_verify_cursor"])

    def test_stop_latency_under_2s_real_clock(self):
        blob = os.urandom(8 << 20)
        vault, _ = make_vault(self.root, {f"s{i}.sav": blob + bytes([i]) for i in range(30)})
        flag = {"v": False}
        m = agent.Maintenance(self.data, game_busy=lambda: flag["v"], battery=lambda: self.env.battery,
                              log=lambda s: None, vault_dir=vault)       # dong ho that, stop_check_s mac dinh 0.5
        t = {}

        # Game "bat dau" ngay khi object dau tien dang duoc bam (khong dung Timer: tren may nhanh
        # cong viec xong truoc khi Timer kip chay -> test chap chon).
        real = agent.hash_file_interruptible

        def spy(path, stop, chunk=1 << 18):
            if "flip" not in t:
                flag["v"] = True
                t["flip"] = time.monotonic()
            return real(path, stop, chunk)
        m._next_tick = None
        with mock.patch.object(agent, "hash_file_interruptible", spy), \
                mock.patch.object(agent, "open", slow_open, create=True):
            m.tick()
        done = time.monotonic()
        v = self.state()["vault_verify"]
        self.assertTrue(v["partial"], f"phai bi ngat giua chung: {v}")
        self.assertLess(done - t["flip"], 2.0)


# --------------------------------------------------------------------------- 3. bam truoc ROM

class TestPrewarm(Base):
    def setUp(self):
        super().setUp()
        self.card = self.root / "sd"
        rom = self.card / "Roms" / "GBA"
        rom.mkdir(parents=True)
        (rom / "a.gba").write_bytes(b"A" * 5000)
        (rom / "b.gba").write_bytes(b"A" * 5000)          # cung size voi a -> ung vien
        (rom / "c.gba").write_bytes(b"C" * 7000)
        (rom / "d.gba").write_bytes(b"D" * 7000)          # cung size voi c -> ung vien
        (rom / "solo.gba").write_bytes(b"S" * 123)        # size duy nhat -> KHONG bi bam
        for p in rom.iterdir():                           # mtime cu de cache tin cay duoc
            os.utime(p, (1_000_000_000, 1_000_000_000))

    def scan(self):
        return InventoryEngine(self.card, None).scan_all(), CATALOG

    def pm(self, **kw):
        m = self.maint(scan=self.scan, paths=(self.card, None), **kw)
        self.env.advance(agent.PREWARM_IDLE_S + 1)        # luc khoi dong coi nhu vua co game -> doi 10 phut
        return m

    def test_runs_only_when_charging(self):
        m = self.pm()
        self.tick(m)                                      # Discharging
        self.assertNotIn("prewarm", self.state() if (self.data / agent.STATE_NAME).exists() else {})
        self.env.battery = {"available": True, "percent": 30, "status": "Charging"}
        self.tick(m)
        p = self.state()["prewarm"]
        self.assertEqual((p["hashed"], p["remaining"], p["done"]), (4, 0, True))
        self.assertIsInstance(p["ts"], int)
        with HashCache(self.data) as c:                   # ung vien da vao cache, file solo thi khong
            rom = self.card / "Roms" / "GBA"
            self.assertEqual(c.get(rom / "a.gba"), sha(b"A" * 5000))
            self.assertIsNotNone(c.get(rom / "d.gba"))
            self.assertIsNone(c.get(rom / "solo.gba"))

    def test_full_status_counts_as_power(self):
        self.env.battery = {"available": True, "percent": 100, "status": "Full"}
        self.tick(self.pm())
        self.assertTrue(self.state()["prewarm"]["done"])

    def test_not_while_game_or_within_10_min_of_game(self):
        self.env.battery = {"available": True, "percent": 30, "status": "Charging"}
        m = self.pm()
        self.env.game = True
        self.tick(m)
        self.env.game = False
        self.tick(m)                                      # moi ~31 s ke tu game cuoi
        self.assertFalse((self.data / agent.STATE_NAME).exists())
        self.env.advance(agent.PREWARM_IDLE_S)
        self.tick(m)
        self.assertTrue(self.state()["prewarm"]["done"])

    def test_stops_on_unplug_and_continues_later(self):
        self.env.battery = {"available": True, "percent": 30, "status": "Charging"}
        e = self.env
        real_sleep = e.sleep

        def unplug_after_first(s):
            real_sleep(s)
            e.battery = {"available": True, "percent": 30, "status": "Discharging"}
        m = self.pm(sleep=unplug_after_first)
        self.tick(m)
        p = self.state()["prewarm"]
        self.assertFalse(p["done"], p)
        self.assertEqual(p["hashed"], 1)
        self.assertEqual(p["remaining"], 3)
        # cam sac lai, qua thoi gian cho: hash tiep, file da bam khong bam lai (cache)
        e.battery = {"available": True, "percent": 30, "status": "Charging"}
        e.advance(agent.PREWARM_RETRY_S)
        m.sleep = real_sleep
        self.tick(m)
        p = self.state()["prewarm"]
        self.assertEqual((p["hashed"], p["remaining"], p["done"]), (3, 0, True))

    def test_game_during_candidate_scan_stops_before_any_hashing(self):
        self.env.battery = {"available": True, "percent": 30, "status": "Charging"}
        n = {"v": 0}

        def busy():
            n["v"] += 1
            return n["v"] >= 4              # tick, sau scan, roi ngay trong find_duplicates
        m = self.pm(game_busy=busy)
        self.tick(m)
        p = self.state()["prewarm"]
        self.assertEqual((p["hashed"], p["done"]), (0, False))
        self.assertIsInstance(p["remaining"], int)
        with HashCache(self.data) as c:
            self.assertIsNone(c.get(self.card / "Roms" / "GBA" / "a.gba"))

    def test_at_most_one_full_run_per_day(self):
        self.env.battery = {"available": True, "percent": 30, "status": "Charging"}
        m = self.pm()
        self.tick(m)
        ts1 = self.state()["prewarm"]["ts"]
        self.env.advance(3600)
        self.tick(m)
        self.assertEqual(self.state()["prewarm"]["ts"], ts1)
        self.env.advance(86400)
        self.tick(m)
        self.assertGreater(self.state()["prewarm"]["ts"], ts1)

    def test_stop_latency_under_2s_real_clock(self):
        rom = self.card / "Roms" / "GBA"
        blob = os.urandom(40 << 20)
        for n in ("big1.gba", "big2.gba", "big3.gba"):
            (rom / n).write_bytes(blob)
            os.utime(rom / n, (1_000_000_000, 1_000_000_000))
        flag = {"v": False}
        m = agent.Maintenance(self.data, game_busy=lambda: flag["v"],
                              battery=lambda: {"available": True, "percent": 50, "status": "Charging"},
                              log=lambda s: None, scan=self.scan, paths=(self.card, None))
        m._last_game -= agent.PREWARM_IDLE_S + 1          # coi nhu da yen tu lau
        t = {}

        real = agent.hash_file_interruptible

        def spy(path, stop, chunk=1 << 18):
            if "flip" not in t and Path(path).name.startswith("big"):   # ngat GIUA mot file lon
                flag["v"] = True
                t["flip"] = time.monotonic()
            return real(path, stop, chunk)
        with mock.patch.object(agent, "hash_file_interruptible", spy), \
                mock.patch.object(agent, "open", slow_open, create=True):
            m.tick()
        done = time.monotonic()
        p = self.state()["prewarm"]
        self.assertFalse(p["done"], p)
        self.assertGreater(p["remaining"], 0)
        self.assertLess(done - t["flip"], 2.0)


# --------------------------------------------------------------------------- 4. don goi cap nhat

PAK_FILES = {"LoongMate.pak/launch.sh": b"#!/bin/sh\necho\n"}


def make_bundle(base, version, created_at):
    base = Path(base)
    for rel, d in PAK_FILES.items():
        p = base.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(d + version.encode())
    m = {"schema": 1, "version": version, "created_at": created_at,
         "files": {rel: sha(d + version.encode()) for rel, d in PAK_FILES.items()}}
    (base / "manifest.json").write_text(json.dumps(m), encoding="utf-8")


class TestPruneBundles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sd = Path(self._tmp.name) / "sd"
        self.apps = self.sd / "Apps" / "mlp1"
        self.apps.mkdir(parents=True)
        self.data = self.sd / ".userdata" / "mlp1" / "LoongMate"
        self.upd = self.sd / "LoongMate-update"
        for i in range(1, 5):
            make_bundle(self.upd / f"b{i}", f"v{i}", f"2026-10-0{i} 10:00:00")

    def names(self):
        return sorted(p.name for p in self.upd.iterdir())

    def test_keeps_two_newest_including_installed(self):
        r = up.apply_update(self.upd / "b4", self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pruned_bundles"], ["b1", "b2"])
        self.assertEqual(self.names(), ["b3", "b4"])

    def test_installed_older_bundle_is_kept_plus_newest_other(self):
        r = up.apply_update(self.upd / "b2", self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.names(), ["b2", "b4"])

    def test_only_valid_manifest_dirs_are_deleted(self):
        (self.upd / "notes").mkdir()
        (self.upd / "notes" / "x.txt").write_text("keep me")
        (self.upd / "broken").mkdir()
        (self.upd / "broken" / "manifest.json").write_text("{not json")
        (self.upd / "readme.txt").write_text("keep")
        up.apply_update(self.upd / "b4", self.apps, self.data)
        self.assertEqual(self.names(), ["b3", "b4", "broken", "notes", "readme.txt"])

    def test_never_deletes_outside_update_dir(self):
        elsewhere = self.sd / "Backup"
        make_bundle(elsewhere / "old1", "v0", "2020-01-01 00:00:00")
        make_bundle(elsewhere / "old2", "v00", "2020-01-02 00:00:00")
        make_bundle(elsewhere / "cur", "vx", "2026-12-01 00:00:00")
        before = sorted(str(p.relative_to(self.sd)) for p in elsewhere.rglob("*"))
        r = up.apply_update(elsewhere / "cur", self.apps, self.data)       # goi nam NGOAI LoongMate-update
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["pruned_bundles"], [])
        self.assertEqual(sorted(str(p.relative_to(self.sd)) for p in elsewhere.rglob("*")), before)
        self.assertEqual(self.names(), ["b1", "b2", "b3", "b4"])           # va khong dung toi LoongMate-update

    def test_failed_update_prunes_nothing(self):
        (self.upd / "b4" / "LoongMate.pak" / "launch.sh").write_bytes(b"tampered")
        r = up.apply_update(self.upd / "b4", self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(self.names(), ["b1", "b2", "b3", "b4"])


if __name__ == "__main__":
    unittest.main()


class LeafConfigSnapshotTests(Base):
    """Agent tu sao luu cau hinh Leaf moi ngay va ghi leaf_updated khi Leaf doi phien ban."""

    def setUp(self):
        super().setUp()
        self.sd = self.root / "sd"
        u = self.sd / ".umrk" / "mlp1"
        (u / "retroarch").mkdir(parents=True)
        (u / "retroarch" / "retroarch.cfg").write_text('video_smooth = "false"\n', encoding="utf-8")
        self.rel = u / "release.json"
        self.rel.write_text('{"version": "0.11.0"}', encoding="utf-8")

    def tick(self, m):
        m._next_tick = None
        m.tick()

    def test_daily_snapshot_and_leaf_update_detected(self):
        from engine import leafconfig
        m = self.maint(paths=(self.sd, None))
        self.tick(m)
        snaps = leafconfig.list_snapshots(self.data)
        self.assertEqual([s["leaf_version"] for s in snaps], ["0.11.0"])
        self.tick(m)
        self.assertEqual(len(leafconfig.list_snapshots(self.data)), 1, "trong ngay khong chup lai")
        self.rel.write_text('{"version": "0.13.0"}', encoding="utf-8")
        self.env.advance(60)
        self.tick(m)
        up = self.state()["leaf_updated"]
        self.assertEqual((up["from"], up["to"], up["before_id"]), ("0.11.0", "0.13.0", snaps[0]["id"]))
        self.assertEqual(leafconfig.list_snapshots(self.data)[0]["id"], up["after_id"])

    def test_no_snapshot_while_game_running(self):
        from engine import leafconfig
        self.env.game = True
        self.tick(self.maint(paths=(self.sd, None)))
        self.assertEqual(leafconfig.list_snapshots(self.data), [])
