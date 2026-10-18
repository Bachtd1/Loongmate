"""
Tests cho engine/batchfix.py và engine/bios.py (hành vi thật trên thẻ giả trong thư mục tạm).

Chứng minh test biết đỏ: đặt LOONGMATE_BATCHFIX_PATH / LOONGMATE_BIOS_PATH tới bản cố tình
làm hỏng của module; test sẽ chạy bản đó thay vì bản thật.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.doctor import LibraryDoctor
from engine.inventory import InventoryEngine


def _load(env, default):
    p = os.environ.get(env)
    if p:
        spec = importlib.util.spec_from_file_location("under_test_" + env.lower(), p)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    return importlib.import_module(default)


BF = _load("LOONGMATE_BATCHFIX_PATH", "engine.batchfix")
BIOS = _load("LOONGMATE_BIOS_PATH", "engine.bios")


def tree(root: Path):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_bf_"))
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        self.ud = self.tmp / "ud" / "LoongMate"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir(parents=True)
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def w(self, p: Path, data: bytes) -> Path:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def plan(self):
        doc = LibraryDoctor(self.sd1, self.sd2, ROOT / "systems.json", self.ud)
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        audit = doc.audit(inv, {})
        return BF.plan_batch_fix(inv, audit, self.sd1, self.sd2, doc.systems_catalog)

    def run_kind(self, kind, plan=None):
        plan = plan or self.plan()
        return BF.execute_batch_fix(plan, self.ud, selected_kinds=[kind]), plan

    @property
    def journal(self):
        return self.ud / "batchfix-journal.jsonl"


class LegacyFolderTests(Base):
    def test_merge_alias_into_canonical(self):
        self.w(self.sd1 / "Roms/PS/a.chd", b"AAA")
        self.w(self.sd1 / "Roms/PS/sub/b.chd", b"BBB")
        self.w(self.sd1 / "Roms/PSX/c.chd", b"CCC")
        self.w(self.sd1 / "Images/PS/a.png", b"IMG")
        plan = self.plan()
        kinds = [s["kind"] for s in plan["steps"] if s["kind"] == "legacy_folder"]
        self.assertEqual(len(kinds), 2)  # Roms/PS và Images/PS
        res = BF.execute_batch_fix(plan, self.ud, selected_kinds=["legacy_folder"])
        self.assertEqual(res["refused"], [])
        self.assertEqual((self.sd1 / "Roms/PSX/a.chd").read_bytes(), b"AAA")
        self.assertEqual((self.sd1 / "Roms/PSX/sub/b.chd").read_bytes(), b"BBB")
        self.assertEqual((self.sd1 / "Roms/PSX/c.chd").read_bytes(), b"CCC")
        self.assertEqual((self.sd1 / "Images/PSX/a.png").read_bytes(), b"IMG")
        self.assertFalse((self.sd1 / "Roms/PS").exists())
        self.assertFalse((self.sd1 / "Images/PS").exists())

    def test_same_name_different_hash_is_skipped_and_source_untouched(self):
        self.w(self.sd1 / "Roms/PS/g.chd", b"SOURCE-VERSION")
        self.w(self.sd1 / "Roms/PS/ok.chd", b"OK")
        self.w(self.sd1 / "Roms/PSX/g.chd", b"DEST-VERSION")
        res, _ = self.run_kind("legacy_folder")
        self.assertEqual((self.sd1 / "Roms/PS/g.chd").read_bytes(), b"SOURCE-VERSION")
        self.assertEqual((self.sd1 / "Roms/PSX/g.chd").read_bytes(), b"DEST-VERSION")
        self.assertTrue(any("KHÁC" in r["reason"] and r["path"].endswith("g.chd") for r in res["refused"]))
        self.assertEqual((self.sd1 / "Roms/PSX/ok.chd").read_bytes(), b"OK")
        self.assertTrue((self.sd1 / "Roms/PS").is_dir(), "còn sót file thì không được xoá thư mục alias")

    def test_same_name_same_hash_goes_to_quarantine(self):
        self.w(self.sd1 / "Roms/PS/g.chd", b"SAME")
        self.w(self.sd1 / "Roms/PSX/g.chd", b"SAME")
        res, _ = self.run_kind("legacy_folder")
        self.assertEqual(res["refused"], [])
        self.assertFalse((self.sd1 / "Roms/PS").exists())
        self.assertEqual((self.sd1 / "Roms/PSX/g.chd").read_bytes(), b"SAME")
        q = [p for p in (self.ud / "Quarantine").rglob("g.chd")]
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0].read_bytes(), b"SAME")

    def test_multifile_group_stays_together_when_one_file_conflicts(self):
        cue = b'FILE "g.bin" BINARY\n  TRACK 01 MODE2/2352\n'
        self.w(self.sd1 / "Roms/PS/g.cue", cue)
        self.w(self.sd1 / "Roms/PS/g.bin", b"SRC-BIN")
        self.w(self.sd1 / "Roms/PSX/g.bin", b"OTHER-BIN")
        self.run_kind("legacy_folder")
        self.assertTrue((self.sd1 / "Roms/PS/g.cue").exists(), "cue không được tách khỏi bin")
        self.assertFalse((self.sd1 / "Roms/PSX/g.cue").exists())

    def test_undo_restores_original_paths(self):
        self.w(self.sd1 / "Roms/PS/a.chd", b"AAA")
        self.w(self.sd1 / "Roms/PS/sub/b.chd", b"BBB")
        self.w(self.sd1 / "Roms/PS/dup.chd", b"DUP")
        self.w(self.sd1 / "Roms/PSX/dup.chd", b"DUP")
        self.w(self.sd1 / "Images/PS/a.png", b"IMG")
        before = tree(self.sd1)
        res, _ = self.run_kind("legacy_folder")
        self.assertNotEqual(tree(self.sd1), before)
        u = BF.undo_batch_fix(self.journal)
        self.assertEqual(u["skipped"], [])
        self.assertEqual(tree(self.sd1), before)
        self.assertTrue((self.sd1 / "Roms/PS/sub").is_dir())
        self.assertFalse((self.sd1 / "Roms/PSX/a.chd").exists())
        # undo lần hai là no-op
        again = BF.undo_batch_fix(self.journal)
        self.assertEqual(again["undone"], [])

    def test_tampered_plan_outside_card_is_refused(self):
        outside = self.tmp / "outside"
        self.w(outside / "Roms/PS/x.chd", b"X")
        plan = self.plan()
        plan["steps"] = [{"kind": "legacy_folder", "label": "x", "data": {
            "card_root": str(outside), "section": "Roms", "alias": "PS", "canonical": "PSX"}}]
        res = BF.execute_batch_fix(plan, self.ud)
        self.assertTrue(res["refused"])
        self.assertTrue((outside / "Roms/PS/x.chd").exists())


class CueTests(Base):
    CUE = b'FILE "HM_BTN_patched.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'

    def setUp(self):
        super().setUp()
        self.cue = self.w(self.sd1 / "Roms/PSX/Vi-HM_BTN_patched.cue", self.CUE)
        self.w(self.sd1 / "Roms/PSX/Vi-HM_BTN_patched.bin", b"BIN")

    def test_backup_made_before_fix_and_undo_restores_bytes(self):
        res, plan = self.run_kind("fix_cue")
        self.assertEqual(res["refused"], [])
        bak = self.cue.with_name(self.cue.name + ".bak-loongmate")
        self.assertEqual(bak.read_bytes(), self.CUE)
        fixed = self.cue.read_bytes()
        self.assertIn(b'FILE "Vi-HM_BTN_patched.bin"', fixed)
        self.assertIn(b"\r\n", fixed)  # giữ nguyên CRLF
        u = BF.undo_batch_fix(self.journal)
        self.assertEqual(u["skipped"], [])
        self.assertEqual(self.cue.read_bytes(), self.CUE)
        self.assertFalse(bak.exists())

    def test_existing_backup_is_not_overwritten(self):
        bak = self.cue.with_name(self.cue.name + ".bak-loongmate")
        bak.write_bytes(b"OLD-BACKUP")
        res, _ = self.run_kind("fix_cue")
        self.assertTrue(res["refused"])
        self.assertEqual(bak.read_bytes(), b"OLD-BACKUP")
        self.assertEqual(self.cue.read_bytes(), self.CUE)

    def test_undo_does_not_clobber_later_manual_edit(self):
        self.run_kind("fix_cue")
        self.cue.write_bytes(b"USER EDITED")
        u = BF.undo_batch_fix(self.journal)
        self.assertTrue(u["skipped"])
        self.assertEqual(self.cue.read_bytes(), b"USER EDITED")


class M3uTests(Base):
    def test_m3u_in_numeric_disc_order_with_representative_files(self):
        for n in (1, 2, 10):
            self.w(self.sd2 / f"Roms/PSX/Final Fantasy IX (USA) (Disc {n}).cue",
                   f'FILE "Final Fantasy IX (USA) (Disc {n}).bin" BINARY\n'.encode())
            self.w(self.sd2 / f"Roms/PSX/Final Fantasy IX (USA) (Disc {n}).bin", b"B%d" % n)
        res, plan = self.run_kind("create_m3u")
        self.assertEqual(res["refused"], [])
        m3u = self.sd2 / "Roms/PSX/Final Fantasy IX (USA).m3u"
        self.assertEqual(m3u.read_text(encoding="utf-8").splitlines(),
                         [f"Final Fantasy IX (USA) (Disc {n}).cue" for n in (1, 2, 10)])
        u = BF.undo_batch_fix(self.journal)
        self.assertFalse(m3u.exists())
        self.assertEqual(u["skipped"], [])

    def test_single_disc_with_tracks_gets_no_m3u(self):
        self.w(self.sd1 / "Roms/PSX/Solo (Disc 1).cue", b'FILE "Solo (Disc 1).bin" BINARY\n')
        self.w(self.sd1 / "Roms/PSX/Solo (Disc 1).bin", b"B")
        self.assertEqual([s for s in self.plan()["steps"] if s["kind"] == "create_m3u"], [])

    def test_create_dirs(self):
        shutil.rmtree(self.sd2 / "Saves")
        res, _ = self.run_kind("create_dirs")
        self.assertTrue((self.sd2 / "Saves").is_dir())
        BF.undo_batch_fix(self.journal)
        self.assertFalse((self.sd2 / "Saves").exists())


class BiosTests(Base):
    GOOD = b"good-bios-bytes"
    ALT = b"alt-region-bios"

    def setUp(self):
        super().setUp()
        md5 = lambda b: hashlib.md5(b).hexdigest()
        self.hashes = self.tmp / "hashes.json"
        self.hashes.write_text(json.dumps({
            "scph5501.bin": {"md5": [md5(self.GOOD), md5(self.ALT)], "source": "u", "systems": ["PSX"]},
            "scph5500.bin": {"md5": [md5(self.GOOD)], "source": "u", "systems": ["PSX"]},
        }), encoding="utf-8")
        self.cat = {"PSX": {"id": "PSX", "folder": "PSX",
                            "bios": ["scph5500.bin", "scph5501.bin", "scph5502.bin", "nohash.bin"]}}

    def check(self):
        out = BIOS.check_bios(self.sd1, self.sd2, self.cat, ["PSX"], hashes_path=self.hashes)
        return {r["name"]: r for r in out["systems"][0]["required"]}

    def test_statuses(self):
        self.w(self.sd1 / "BIOS/scph5500.bin", self.GOOD)
        self.w(self.sd2 / "BIOS/sub/SCPH5501.BIN", self.ALT)  # thư mục con, khác hoa/thường, md5 thứ hai
        self.w(self.sd1 / "BIOS/nohash.bin", b"whatever")
        r = self.check()
        self.assertEqual(r["scph5500.bin"]["status"], "ok")
        self.assertEqual(r["scph5501.bin"]["status"], "ok")
        self.assertEqual(r["scph5502.bin"]["status"], "missing")
        self.assertIsNone(r["scph5502.bin"]["found_path"])
        self.assertIn("Nhập game", r["scph5502.bin"]["note"])
        self.assertEqual(r["nohash.bin"]["status"], "unknown_hash")
        self.assertEqual(r["nohash.bin"]["md5"], hashlib.md5(b"whatever").hexdigest())

    def test_wrong_hash(self):
        self.w(self.sd1 / "BIOS/scph5500.bin", b"corrupted")
        r = self.check()["scph5500.bin"]
        self.assertEqual(r["status"], "wrong_hash")
        self.assertIn(hashlib.md5(self.GOOD).hexdigest(), r["note"])
        self.assertIn("không tải BIOS", r["note"])

    def test_good_copy_on_other_card_beats_bad_copy(self):
        self.w(self.sd1 / "BIOS/scph5500.bin", b"corrupted")
        self.w(self.sd2 / "BIOS/scph5500.bin", self.GOOD)
        self.assertEqual(self.check()["scph5500.bin"]["status"], "ok")

    def test_real_hash_file_is_wellformed_and_sourced(self):
        data = json.loads((ROOT / "bios_hashes.json").read_text(encoding="utf-8"))
        for name, ent in data.items():
            if name.startswith("_"):
                continue
            self.assertTrue(ent["source"].startswith("https://docs.libretro.com/"), name)
            for m in ent["md5"]:
                self.assertRegex(m, r"^[0-9a-f]{32}$")


if __name__ == "__main__":
    unittest.main()
