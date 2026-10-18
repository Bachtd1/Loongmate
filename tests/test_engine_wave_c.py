"""
Tests đợt C của engine LoongMate: sửa lỗi 1-7, nâng cấp A (cache hash) và D (sức khoẻ pin).

Mọi test chạy hành vi thật trên thẻ giả trong thư mục tạm. Chứng minh test biết đỏ: đặt biến môi trường
trỏ tới bản cố tình làm hỏng (nạp bằng importlib), test sẽ chạy bản đó:
  LOONGMATE_WAVEC_DOCTOR_PATH     engine/doctor.py
  LOONGMATE_WAVEC_BIOS_PATH       engine/bios.py
  LOONGMATE_WAVEC_TRANSFER_PATH   engine/transfer.py
  LOONGMATE_WAVEC_DEDUPE_PATH     engine/dedupe.py
  LOONGMATE_WAVEC_HASHCACHE_PATH  engine/hashcache.py
  LOONGMATE_WAVEC_SYSTEMS_PATH    systems.json
"""

import datetime
import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(env, default):
    p = os.environ.get(env)
    if p:
        spec = importlib.util.spec_from_file_location("wavec_" + env.lower(), p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    return importlib.import_module(default)


DOC = _load("LOONGMATE_WAVEC_DOCTOR_PATH", "engine.doctor")
BIOS = _load("LOONGMATE_WAVEC_BIOS_PATH", "engine.bios")
TR = _load("LOONGMATE_WAVEC_TRANSFER_PATH", "engine.transfer")
DD = _load("LOONGMATE_WAVEC_DEDUPE_PATH", "engine.dedupe")
HC = _load("LOONGMATE_WAVEC_HASHCACHE_PATH", "engine.hashcache")
SYSTEMS = Path(os.environ.get("LOONGMATE_WAVEC_SYSTEMS_PATH") or ROOT / "systems.json")

from engine import agent as AG
from engine import telemetry
from engine.autobackup import AutoBackup
from engine.inventory import InventoryEngine
from engine.savevault import SaveVault

MB = 1024 * 1024


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_wavec_"))
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        self.ud = self.tmp / "ud" / "LoongMate"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir(parents=True)
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def w(self, p, data=b"x") -> Path:
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def old(self, p, secs=100):
        """Đặt mtime cũ hơn `secs` giây: để cache tin cậy (ngoài cửa sổ 2 giây của FAT32)."""
        t = time.time() - secs
        os.utime(p, (t, t))

    def doctor(self):
        return DOC.LibraryDoctor(self.sd1, self.sd2, SYSTEMS, self.ud)

    def audit(self):
        doc = self.doctor()
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        return doc, doc.audit(inv, {})

    def ids(self, rep, prefix):
        return [i for i in rep["issues"] if i["id"].startswith(prefix)]


# ---------------------------------------------------------------- #1 doctor.fix_cue
class TestDoctorFixCue(Base):
    CUE = (b'TITLE "Caf\xe9"\r\nFILE "Other.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n'
           b'    INDEX 01 00:00:00\r\n')

    def _mk(self, cue_bytes):
        cue = self.w(self.sd1 / "Roms/PSX/Game.cue", cue_bytes)
        self.w(self.sd1 / "Roms/PSX/Game.bin", b"DATA")
        doc, rep = self.audit()
        acts = [a for a in rep["fixable_actions"] if a["type"] == "fix_cue"]
        self.assertEqual(len(acts), 1, rep["issues"])
        return doc, cue, acts[0]

    def test_01_latin1_crlf_cue_is_fixed_bytewise_with_backup(self):
        doc, cue, act = self._mk(self.CUE)
        res = doc.execute_fix(act)
        self.assertTrue(res["success"], res)
        self.assertEqual(cue.read_bytes(), self.CUE.replace(b'"Other.bin"', b'"Game.bin"'))
        bak = cue.with_name("Game.cue.bak-loongmate")
        self.assertEqual(bak.read_bytes(), self.CUE)

    def test_02_unquoted_file_line_is_fixed(self):
        raw = b'FILE Other.bin BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
        doc, cue, act = self._mk(raw)
        self.assertTrue(doc.execute_fix(act)["success"])
        self.assertEqual(cue.read_bytes(), raw.replace(b"FILE Other.bin", b'FILE "Game.bin"'))

    def test_03_line_not_found_reports_failure_and_changes_nothing(self):
        doc, cue, act = self._mk(self.CUE)
        res = doc.execute_fix(dict(act, old_str="Nope.bin"))
        self.assertFalse(res["success"])
        self.assertEqual(cue.read_bytes(), self.CUE)
        self.assertFalse(cue.with_name("Game.cue.bak-loongmate").exists())

    def test_04_existing_backup_is_never_overwritten(self):
        doc, cue, act = self._mk(self.CUE)
        bak = self.w(cue.with_name("Game.cue.bak-loongmate"), b"OLD-BACKUP")
        res = doc.execute_fix(act)
        self.assertFalse(res["success"])
        self.assertEqual(bak.read_bytes(), b"OLD-BACKUP")
        self.assertEqual(cue.read_bytes(), self.CUE)

    def test_05_cue_outside_roms_is_refused(self):
        doc, cue, act = self._mk(self.CUE)
        other = self.w(self.sd1 / "Saves/x.cue", self.CUE)
        self.w(self.sd1 / "Saves/Game.bin", b"D")
        res = doc.execute_fix(dict(act, path=str(other)))
        self.assertFalse(res["success"])
        self.assertEqual(other.read_bytes(), self.CUE)


# ---------------------------------------------------------------- #2 doctor multidisc
class TestDoctorMultiDisc(Base):
    def test_06_one_disc_with_many_tracks_is_not_multidisc(self):
        self.w(self.sd1 / "Roms/PSX/Game (Disc 1).cue", b'FILE "Game (Disc 1).bin" BINARY\r\n')
        self.w(self.sd1 / "Roms/PSX/Game (Disc 1).bin", b"D")
        _, rep = self.audit()
        self.assertEqual(self.ids(rep, "MULTIDISC_NO_M3U"), [])
        self.assertEqual([a for a in rep["fixable_actions"] if a["type"] == "create_m3u"], [])

    def test_07_real_multidisc_flagged_once_ordered_by_disc_number_one_file_per_disc(self):
        for n in (1, 2, 10):
            self.w(self.sd1 / f"Roms/PSX/Game (Disc {n}).cue", f'FILE "Game (Disc {n}).bin" BINARY\r\n'.encode())
            self.w(self.sd1 / f"Roms/PSX/Game (Disc {n}).bin", b"D")
        _, rep = self.audit()
        iss = self.ids(rep, "MULTIDISC_NO_M3U")
        self.assertEqual(len(iss), 1, rep["issues"])
        want = [f"Game (Disc {n}).cue" for n in (1, 2, 10)]
        self.assertEqual(iss[0]["discs"], want)
        act = [a for a in rep["fixable_actions"] if a["type"] == "create_m3u"]
        self.assertEqual([a["lines"] for a in act], [want])

    def test_08_existing_m3u_silences_the_warning(self):
        for n in (1, 2):
            self.w(self.sd1 / f"Roms/PSX/Game (Disc {n}).cue", f'FILE "Game (Disc {n}).bin" BINARY\r\n'.encode())
            self.w(self.sd1 / f"Roms/PSX/Game (Disc {n}).bin", b"D")
        self.w(self.sd1 / "Roms/PSX/Game.m3u", b"Game (Disc 1).cue\nGame (Disc 2).cue\n")
        _, rep = self.audit()
        self.assertEqual(self.ids(rep, "MULTIDISC_NO_M3U"), [])


# ---------------------------------------------------------------- #3 BIOS any_of
class TestBiosAnyOf(Base):
    """Cơ chế nhóm BIOS thay thế nhau, kiểm trên hệ KHÔNG có BIOS giả lập (bỏ bios_hle khỏi danh mục của test).
    Hành vi khi có BIOS giả lập (PSX thật: PCSX-ReARMed bios=auto): test_13."""
    GOOD = b"good-bios"

    def doctor(self, keep_hle=False):
        doc = super().doctor()
        if not keep_hle:
            for v in doc.systems_catalog.values():
                v.pop("bios_hle", None)
        return doc

    def setUp(self):
        super().setUp()
        md5 = lambda b: hashlib.md5(b).hexdigest()
        self.hashes = self.tmp / "hashes.json"
        self.hashes.write_text(json.dumps({
            n: {"md5": [md5(self.GOOD)], "source": "u", "systems": ["PSX"]}
            for n in ("scph5500.bin", "scph5501.bin", "scph5502.bin")}), encoding="utf-8")
        self.w(self.sd1 / "Roms/PSX/g.chd", b"CHD")

    def check(self):
        cat = self.doctor().systems_catalog
        out = BIOS.check_bios(self.sd1, self.sd2, cat, ["PSX"], hashes_path=self.hashes)
        return out["systems"][0]

    def test_09_doctor_one_region_bios_is_enough(self):
        self.w(self.sd1 / "BIOS/scph5501.bin", self.GOOD)
        _, rep = self.audit()
        self.assertEqual(self.ids(rep, "MISSING_BIOS"), [], rep["issues"])

    def test_10_doctor_missing_whole_group_is_one_warning(self):
        _, rep = self.audit()
        iss = self.ids(rep, "MISSING_BIOS")
        self.assertEqual(len(iss), 1, iss)
        self.assertEqual(iss[0]["severity"], "warning")
        self.assertEqual(sorted(iss[0]["missing"]), ["scph5500.bin", "scph5501.bin", "scph5502.bin"])

    def test_11_check_bios_group_satisfied_by_one_ok_file(self):
        self.w(self.sd2 / "BIOS/scph5502.bin", self.GOOD)
        s = self.check()
        rows = {r["name"]: r for r in s["required"]}
        self.assertEqual(rows["scph5502.bin"]["status"], "ok")
        self.assertFalse(rows["scph5502.bin"]["optional"])
        for n in ("scph5500.bin", "scph5501.bin"):
            self.assertEqual(rows[n]["status"], "missing")
            self.assertTrue(rows[n]["optional"], "file còn lại trong nhóm không được coi là thiếu")
        self.assertEqual(len(s["any_of"]), 1)
        self.assertTrue(s["any_of"][0]["satisfied"])
        self.assertEqual(s["any_of"][0]["severity"], "ok")

    def test_13_emulated_bios_makes_missing_group_a_note(self):
        doc = self.doctor(keep_hle=True)
        rep = doc.audit(InventoryEngine(self.sd1, self.sd2).scan_all(), {})
        self.assertEqual(self.ids(rep, "MISSING_BIOS"), [])
        self.assertEqual([i["severity"] for i in self.ids(rep, "BIOS_HLE_PSX")], ["info"])
        s = BIOS.check_bios(self.sd1, self.sd2, doc.systems_catalog, ["PSX"], hashes_path=self.hashes)["systems"][0]
        self.assertEqual(s["any_of"][0]["severity"], "info")
        self.assertIn("PCSX-ReARMed", s["hle"])

    def test_12_check_bios_whole_group_missing_or_wrong_hash_is_warning(self):
        s = self.check()
        self.assertFalse(s["any_of"][0]["satisfied"])
        self.assertEqual(s["any_of"][0]["severity"], "warning")
        self.assertTrue(all(not r["optional"] for r in s["required"]))
        self.w(self.sd1 / "BIOS/scph5500.bin", b"corrupted")
        s = self.check()
        self.assertFalse(s["any_of"][0]["satisfied"])
        self.assertEqual(s["any_of"][0]["status"], "wrong_hash")
        self.assertEqual(s["any_of"][0]["severity"], "warning")


# ---------------------------------------------------------------- #4 plan_bulk_move
class TestBulkGameUnits(Base):
    def setUp(self):
        super().setUp()
        self.free = {"sd1": 900 * MB, "sd2": 25 * 1024 * MB}
        p = mock.patch.object(TR, "_free_bytes",
                              lambda root: self.free["sd1" if str(Path(root).resolve()).startswith(
                                  str(self.sd1.resolve())) else "sd2"])
        p.start()
        self.addCleanup(p.stop)

    def bulk(self):
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        # cần 100 MB mà thư viện chỉ có vài MB -> mọi đơn vị game đều được đề xuất
        return TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=900 + 100)

    def disc(self, folder, title, n, size=MB):
        base = f"{title} (Disc {n})"
        self.w(folder / f"{base}.cue", f'FILE "{base}.bin" BINARY\r\n'.encode())
        self.w(folder / f"{base}.bin", b"B" * size)
        for p in (folder / f"{base}.cue", folder / f"{base}.bin"):
            self.old(p, 86400 * 10)

    def test_13_m3u_game_is_one_candidate_named_after_game(self):
        f = self.sd1 / "Roms/PSX"
        for n in (1, 2):
            self.disc(f, "Game A", n)
        self.w(f / "Game A.m3u", b"Game A (Disc 1).cue\nGame A (Disc 2).cue\n")
        self.old(f / "Game A.m3u", 86400 * 10)
        rep = self.bulk()
        self.assertEqual([m["name"] for m in rep["moves"]], ["Game A"], rep)
        names = {Path(e["src"]).name for e in rep["moves"][0]["plan"]["files"]}
        self.assertEqual(names, {"Game A.m3u", "Game A (Disc 1).cue", "Game A (Disc 2).cue",
                                 "Game A (Disc 1).bin", "Game A (Disc 2).bin"})
        # không đề xuất .cue riêng khi đã có .m3u chứa nó
        self.assertFalse(any(m["rom_path"].endswith(".cue") for m in rep["moves"]))

    def test_14_multidisc_without_m3u_is_one_candidate_and_moves_all_discs(self):
        f = self.sd1 / "Roms/PSX"
        for n in (1, 2, 3):
            self.disc(f, "Game B", n)
        rep = self.bulk()
        self.assertEqual([m["name"] for m in rep["moves"]], ["Game B"], rep)
        self.assertEqual(rep["moves"][0]["discs"], 3)
        self.assertEqual(rep["bytes_freed"], 3 * MB + 3 * len('FILE "Game B (Disc 1).bin" BINARY\r\n'))
        res = TR.execute_move(rep["moves"][0]["plan"], self.ud, self.sd1, self.sd2)
        self.assertEqual(res["refused"], [])
        for n in (1, 2, 3):
            self.assertTrue((self.sd2 / f"Roms/PSX/Game B (Disc {n}).bin").is_file())
            self.assertFalse((self.sd1 / f"Roms/PSX/Game B (Disc {n}).bin").exists())

    def test_15_single_disc_many_tracks_and_plain_rom_are_single_units(self):
        f = self.sd1 / "Roms/PSX"
        self.disc(f, "Game C", 1)
        self.w(self.sd1 / "Roms/GBA/Plain.gba", b"R" * MB)
        self.old(self.sd1 / "Roms/GBA/Plain.gba", 86400 * 10)
        rep = self.bulk()
        self.assertEqual(sorted(m["name"] for m in rep["moves"]), ["Game C (Disc 1)", "Plain.gba"], rep)


# ---------------------------------------------------------------- #5 agent log rotation
class TestAgentLogRotation(Base):
    def test_16_prune_logs_keeps_14_days_and_ignores_foreign_files(self):
        logs = self.ud / "logs"
        today = datetime.date(2026, 10, 2)
        names = {}
        for back in (0, 1, 13, 14, 15, 200):
            d = today - datetime.timedelta(days=back)
            names[back] = f"agent_{d:%Y%m%d}.log"
            self.w(logs / names[back])
        for other in ("notes.txt", "agent_abcdefgh.log", "agent_20261399.log", "agent_20260101.log.bak"):
            self.w(logs / other)
        removed = AG.prune_logs(self.ud, today=today)
        self.assertEqual(sorted(removed), sorted(names[b] for b in (14, 15, 200)))
        left = sorted(p.name for p in logs.iterdir())
        for keep in (0, 1, 13):
            self.assertIn(names[keep], left)
        for other in ("notes.txt", "agent_abcdefgh.log", "agent_20261399.log", "agent_20260101.log.bak"):
            self.assertIn(other, left)
        self.assertEqual(AG.prune_logs(self.tmp / "no-such-dir"), [])

    def test_17_agent_run_prunes_old_logs_at_startup(self):
        old = self.w(self.ud / "logs/agent_20200101.log")
        r = subprocess.run([sys.executable, "-m", "engine.agent", "--run", "--userdata", str(self.ud),
                            "--primary", str(self.sd1), "--duration", "1", "--interval", "1", "--poll", "0.5"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(old.exists(), r.stdout)
        self.assertTrue(list((self.ud / "logs").glob(f"agent_{time.strftime('%Y%m%d')}.log")))


# ---------------------------------------------------------------- #6 autobackup
class TestAutoBackupCleanup(Base):
    def test_18_no_stale_keep_auto_and_no_snapshot_listing_or_warning(self):
        class Vault:
            def create_snapshot(self, destination=None, label="manual"):
                return {"success": True, "snapshot_id": "snap_x_auto", "files_count": 1}

            def list_snapshots(self):
                raise AssertionError("AutoBackup không được tự liệt kê/cảnh báo snapshot nữa")

        with self.assertRaises(TypeError):
            AutoBackup(Vault(), keep_auto=3)
        logs = []
        ab = AutoBackup(Vault(), log=logs.append, settle_s=0)
        ab.feed([SimpleNamespace(kind="finish", launch_id="1", source="log", detail="")], 0)
        res = ab.tick(1)
        self.assertTrue(res["success"])
        self.assertFalse(any("prune" in m.lower() or "Luu y" in m for m in logs), logs)

    def test_19_prune_keeps_newest_auto_snapshots_even_with_same_second_ids(self):
        vault = SaveVault(self.sd1, None, self.ud, auto_retention=3)
        ids = ["snap_20260101_101010_auto"] + [f"snap_20260101_101010_auto_{n}" for n in (2, 3, 9, 10, 11)]
        for sid in ids:
            d = vault.snapshots_dir / sid
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"committed": True, "snapshot_id": sid, "label": "auto"}))
        vault._prune_old_snapshots()
        kept = sorted(s["id"] for s in vault.list_snapshots())
        self.assertEqual(kept, sorted(f"snap_20260101_101010_auto_{n}" for n in (9, 10, 11)))


# ---------------------------------------------------------------- #7 list_snapshots order
class TestSnapshotOrder(Base):
    def test_20_same_second_ids_sort_by_numeric_suffix(self):
        vault = SaveVault(self.sd1, None, self.ud)
        ids = ["snap_20260101_101010_auto"] + [f"snap_20260101_101010_auto_{n}" for n in (2, 3, 9, 10, 11)]
        ids.append("snap_20260101_101011_auto")
        ids.append("snap_20251231_235959_manual_12")
        for sid in ids:
            d = vault.snapshots_dir / sid
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"committed": True, "snapshot_id": sid, "label": "auto"}))
        got = [s["id"] for s in vault.list_snapshots()]
        want = ["snap_20260101_101011_auto"] + [f"snap_20260101_101010_auto_{n}" for n in (11, 10, 9, 3, 2)] + \
               ["snap_20260101_101010_auto", "snap_20251231_235959_manual_12"]
        self.assertEqual(got, want)


# ---------------------------------------------------------------- A: HashCache
class CacheBase(Base):
    def setUp(self):
        super().setUp()
        self.cache = HC.HashCache(self.ud)
        self.addCleanup(self.cache.close)


class TestHashCache(CacheBase):
    def test_21_hit_after_put_miss_after_change_and_persists_across_instances(self):
        f = self.w(self.tmp / "a.bin", b"hello")
        self.old(f)
        calls = []

        def comp(p):
            calls.append(p)
            return sha(Path(p).read_bytes())
        self.assertEqual(self.cache.hash(f, comp), sha(b"hello"))
        self.assertEqual(self.cache.hash(f, comp), sha(b"hello"))
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.cache.hits, self.cache.misses), (1, 1))
        self.cache.close()
        again = HC.HashCache(self.ud)
        self.addCleanup(again.close)
        self.assertEqual(again.get(f), sha(b"hello"))
        self.assertTrue((self.ud / "cache" / "hashes.sqlite").is_file())
        # đổi kích thước -> không khớp
        f.write_bytes(b"hello world")
        self.old(f)
        self.assertIsNone(again.get(f))
        # đổi mtime (cùng kích thước) -> không khớp
        f.write_bytes(b"HELLO")
        self.old(f, 5000)
        self.assertIsNone(again.get(f))

    def test_22_fat32_two_second_window_is_not_trusted(self):
        """File sửa (cùng kích thước, cùng mtime) trong cửa sổ 2 giây sau lần băm: cache KHÔNG được trả hash cũ."""
        f = self.w(self.tmp / "save.srm", b"AAAA")
        t_ns = time.time_ns() - 500_000_000          # mtime = 0,5 giây trước = trong cửa sổ 2 giây của lúc băm
        os.utime(f, ns=(t_ns, t_ns))
        self.cache.put(f, sha(b"AAAA"))
        f.write_bytes(b"BBBB")                       # cùng kích thước
        os.utime(f, ns=(t_ns, t_ns))                 # FAT32 làm tròn -> cùng mtime
        self.assertIsNone(self.cache.get(f), "mtime nằm trong cửa sổ 2 giây của lần băm thì không được tin cache")
        self.assertEqual(self.cache.hash(f, lambda p: sha(Path(p).read_bytes())), sha(b"BBBB"))

    def test_23_old_mtime_is_trusted_and_future_mtime_is_not(self):
        f = self.w(self.tmp / "old.bin", b"zz")
        self.old(f, 10)
        self.cache.put(f, "H1")
        self.assertEqual(self.cache.get(f), "H1")
        g = self.w(self.tmp / "fut.bin", b"zz")
        t = time.time() + 3600
        os.utime(g, (t, t))
        self.cache.put(g, "H2")
        self.assertIsNone(self.cache.get(g))

    def test_24_file_changed_while_hashing_is_not_stored(self):
        f = self.w(self.tmp / "busy.bin", b"1111")
        self.old(f)

        def comp(p):
            Path(p).write_bytes(b"2222")             # đổi trong lúc băm (mtime đổi)
            self.old(p, 50)
            return "STALE"
        self.assertEqual(self.cache.hash(f, comp), "STALE")
        self.assertIsNone(self.cache.get(f))

    def test_25_prune_missing_removes_only_vanished_files(self):
        a, b = self.w(self.tmp / "a", b"a"), self.w(self.tmp / "b", b"b")
        for p in (a, b):
            self.old(p)
            self.cache.put(p, "h")
        a.unlink()
        self.assertEqual(self.cache.prune_missing(), 1)
        self.assertEqual(self.cache.get(b), "h")
        self.assertEqual(self.cache.prune_missing(), 0)

    def test_26_unwritable_cache_location_degrades_to_no_cache(self):
        blocker = self.w(self.tmp / "blocker", b"file, not dir")
        c = HC.HashCache(blocker)                    # <file>/cache/... không tạo được
        f = self.w(self.tmp / "x", b"x")
        self.old(f)
        self.assertEqual(c.hash(f, lambda p: "H"), "H")
        self.assertIsNone(c.get(f))
        self.assertEqual(c.prune_missing(), 0)


class TestDedupeWithCache(CacheBase):
    CONTENT = b"GBA-ROM-" + bytes(range(256)) * 4

    def find(self, cache):
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        catalog = self.doctor().systems_catalog

        def fake_usage(root):
            return SimpleNamespace(total=0, used=0, free=900 * MB if str(root).startswith(str(self.sd1)) else 25 * 1024 ** 3)
        with mock.patch.object(DD.shutil, "disk_usage", fake_usage):
            return DD.find_duplicates(inv, catalog, self.sd1, self.sd2, cache=cache)

    def test_27_second_scan_hits_cache_without_rehashing_and_reports_stats(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba", self.CONTENT)
        b = self.w(self.sd2 / "Roms/GBA/Game.gba", self.CONTENT)
        for p in (a, b):
            self.old(p)
        r1 = self.find(self.cache)
        self.assertEqual((r1["cache_hits"], r1["cache_misses"]), (0, 2))
        self.assertEqual(len(r1["groups"]), 1)
        with mock.patch.object(DD, "compute_sha256", side_effect=AssertionError("không được băm lại")):
            r2 = self.find(self.cache)
        self.assertEqual((r2["cache_hits"], r2["cache_misses"]), (2, 0))
        self.assertEqual([g["sha256"] for g in r2["groups"]], [g["sha256"] for g in r1["groups"]])
        # không dùng cache: thống kê bằng 0, kết quả như cũ
        r0 = self.find(None)
        self.assertEqual((r0["cache_hits"], r0["cache_misses"]), (0, 0))
        self.assertEqual([g["sha256"] for g in r0["groups"]], [g["sha256"] for g in r1["groups"]])

    def test_28_changed_file_is_rehashed_and_group_disappears(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba", self.CONTENT)
        b = self.w(self.sd2 / "Roms/GBA/Game.gba", self.CONTENT)
        for p in (a, b):
            self.old(p)
        self.assertEqual(len(self.find(self.cache)["groups"]), 1)
        a.write_bytes(self.CONTENT[:-1] + b"!")      # cùng kích thước, khác nội dung
        self.old(a, 50)
        r = self.find(self.cache)
        self.assertEqual((r["cache_hits"], r["cache_misses"]), (1, 1))
        self.assertEqual(r["groups"], [])

    def test_29_lying_cache_may_mislead_find_but_execute_deletion_refuses(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba", b"A" * 64)
        b = self.w(self.sd2 / "Roms/GBA/Game.gba", b"B" * 64)    # cùng kích thước, KHÁC nội dung
        for p in (a, b):
            self.old(p)
            self.cache.put(p, "f" * 64)                          # cache nói dối: cùng hash
        rep = self.find(self.cache)
        self.assertEqual(rep["cache_hits"], 2)
        self.assertEqual(len(rep["groups"]), 1, "cache nói dối nên find_duplicates bị đánh lừa (điều kiện của test)")
        g = rep["groups"][0]
        res = DD.execute_deletion([r["path"] for r in g["remove"]], self.ud, "duplicate",
                                  kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
                                  primary_path=self.sd1, secondary_path=self.sd2)
        self.assertEqual(res["deleted"], [], res)
        self.assertTrue(res["refused"])
        self.assertEqual(a.read_bytes(), b"A" * 64)
        self.assertEqual(b.read_bytes(), b"B" * 64)

    def test_30_lying_cache_on_removed_copy_only_is_also_refused(self):
        """Bản giữ đúng hash thật; bản xoá khác nội dung nhưng cache bảo cùng hash."""
        real = b"R" * 64
        keep = self.w(self.sd2 / "Roms/GBA/Game.gba", real)
        victim = self.w(self.sd1 / "Roms/GBA/Game.gba", b"V" * 64)
        for p in (keep, victim):
            self.old(p)
        self.cache.put(keep, sha(real))
        self.cache.put(victim, sha(real))
        rep = self.find(self.cache)
        self.assertEqual(len(rep["groups"]), 1)
        g = rep["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), keep)
        res = DD.execute_deletion([r["path"] for r in g["remove"]], self.ud, "duplicate",
                                  kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
                                  primary_path=self.sd1, secondary_path=self.sd2)
        self.assertEqual(res["deleted"], [], res)
        self.assertEqual(victim.read_bytes(), b"V" * 64)

    def test_31_honest_duplicates_are_still_deleted(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba", self.CONTENT)
        b = self.w(self.sd2 / "Roms/GBA/Game.gba", self.CONTENT)
        for p in (a, b):
            self.old(p)
        g = self.find(self.cache)["groups"][0]
        res = DD.execute_deletion([r["path"] for r in g["remove"]], self.ud, "duplicate",
                                  kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
                                  primary_path=self.sd1, secondary_path=self.sd2)
        self.assertEqual(len(res["deleted"]), 1, res)
        self.assertEqual(sum(p.exists() for p in (a, b)), 1)


# ---------------------------------------------------------------- D: telemetry / battery health
class TestBatteryTelemetry(Base):
    def power(self, **vals):
        root = self.tmp / "power"
        usb = root / "usb"
        usb.mkdir(parents=True, exist_ok=True)
        (usb / "type").write_text("USB\n")
        (usb / "charge_full").write_text("999\n")
        bat = root / "battery"
        bat.mkdir(parents=True, exist_ok=True)
        (bat / "type").write_text("Battery\n")
        for k, v in vals.items():
            (bat / k).write_text(f"{v}\n")
        return root

    class P:
        def get_battery_info(self):
            return {"available": True, "percent": 80, "status": "Discharging"}

    def test_32_sample_reads_new_battery_fields_from_battery_supply_only(self):
        root = self.power(charge_full=3853000, current_now=-312000, voltage_now=3912000, health="Good")
        row = telemetry.sample(self.P(), self.tmp / "th", self.tmp / "f", now=10, power_root=root)
        self.assertEqual((row["charge_full_uah"], row["current_ua"], row["voltage_uv"], row["health"]),
                         (3853000, -312000, 3912000, "Good"))

    def test_33_unreadable_fields_stay_blank_not_invented(self):
        root = self.power(voltage_now=3900000)               # không có charge_full, current_now, health
        row = telemetry.sample(self.P(), self.tmp / "th", self.tmp / "f", now=10, power_root=root)
        self.assertIsNone(row["charge_full_uah"])
        self.assertIsNone(row["current_ua"])
        self.assertIsNone(row["health"])
        self.assertEqual(row["voltage_uv"], 3900000)
        telemetry.append(self.ud, row)
        back = telemetry.load(self.ud)[0]
        self.assertIsNone(back["charge_full_uah"])
        self.assertEqual(back["voltage_uv"], 3900000)

    def test_34_roundtrip_new_columns_and_legacy_csv_still_reads(self):
        root = self.power(charge_full=3853000, current_now=-1, voltage_now=2, health="Good")
        ts = int(time.time())
        telemetry.append(self.ud, telemetry.sample(self.P(), self.tmp / "th", self.tmp / "f", now=ts, power_root=root))
        month = telemetry._month_file(self.ud, ts)
        # CSV cũ: header 7 cột + dòng 7 cột, rồi dòng mới 11 cột nối tiếp trong cùng file
        month.write_text("ts,battery_pct,status,soc_c,gpu_c,batt_c,cpu_khz\n"
                         f"{ts - 100},50,Discharging,40.0,,,1416000\n", encoding="utf-8")
        telemetry.append(self.ud, telemetry.sample(self.P(), self.tmp / "th", self.tmp / "f", now=ts, power_root=root))
        rows = telemetry.load(self.ud)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["battery_pct"], 50)
        self.assertIsNone(rows[0]["charge_full_uah"])
        self.assertIsNone(rows[0]["health"])
        self.assertEqual(rows[1]["charge_full_uah"], 3853000)
        self.assertEqual(rows[1]["health"], "Good")
        self.assertEqual(telemetry.summarize(rows)["samples"], 2)

    def rows(self, days, step_h=6, start_uah=3853000, uah_per_day=-4000, health="Good"):
        out = []
        n = int(days * 24 / step_h) + 1
        for i in range(n):
            d = i * step_h / 24.0
            out.append({"ts": 1_700_000_000 + int(d * 86400), "battery_pct": 50, "status": "Discharging",
                        "charge_full_uah": int(start_uah + uah_per_day * d), "health": health})
        return out

    def test_35_health_trend_from_ten_days_of_history(self):
        h = telemetry.battery_health(self.rows(10))
        self.assertTrue(h["available"])
        self.assertEqual(h["design_mah"], 4000)
        self.assertEqual(h["design_source"],
                         "thông số công khai Pocket 1 (4000 mAh) — driver không cung cấp charge_full_design")
        self.assertAlmostEqual(h["charge_full_mah"], 3813.0, delta=0.2)      # 3853 - 4*10
        self.assertAlmostEqual(h["estimated_pct"], 95.3, delta=0.1)
        self.assertEqual(h["driver_health"], "Good")
        self.assertAlmostEqual(h["trend_mah_per_30d"], -120.0, delta=0.5)
        self.assertEqual(h["samples"], 41)
        self.assertIn("ƯỚC LƯỢNG", h["reason"])

    def test_36_less_than_seven_days_gives_no_trend_with_reason(self):
        h = telemetry.battery_health(self.rows(5))
        self.assertTrue(h["available"])
        self.assertIsNone(h["trend_mah_per_30d"])
        self.assertIn("7", h["reason"])
        self.assertIsNotNone(h["estimated_pct"])

    def test_37_no_charge_full_data_means_unavailable_not_invented(self):
        legacy = [{"ts": 1_700_000_000 + i * 300, "battery_pct": 50, "status": "Discharging"} for i in range(5)]
        for rows in ([], legacy):
            h = telemetry.battery_health(rows)
            self.assertFalse(h["available"])
            self.assertIsNone(h["charge_full_mah"])
            self.assertIsNone(h["estimated_pct"])
            self.assertIsNone(h["trend_mah_per_30d"])
            self.assertEqual(h["samples"], 0)
            self.assertTrue(h["reason"])

    def test_38_custom_design_capacity_is_labelled_and_used(self):
        h = telemetry.battery_health(self.rows(8), design_mah=5000)
        self.assertEqual(h["design_mah"], 5000)
        self.assertNotIn("Pocket 1 (4000", h["design_source"])
        self.assertAlmostEqual(h["estimated_pct"], h["charge_full_mah"] / 50.0, delta=0.1)


if __name__ == "__main__":
    unittest.main()
