"""
Dot B (loi engine): retention snapshot theo nhan, duong dan save emulator chay rieng,
BIOS khong bat buoc, va pak/build.sh. Moi test chay hanh vi that (khong grep ma nguon).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import bios as BIOS
from engine import savevault as sv_mod
from engine.doctor import LibraryDoctor
from engine.inventory import InventoryEngine
from engine.savevault import SaveVault, snapshot_label

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "systems.json"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_waveb_"))
        self.primary = self.tmp / "sd1"
        self.ud = self.tmp / "ud"
        (self.primary / "Saves").mkdir(parents=True)
        self.ud.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class RetentionByLabel(Base):
    def _fake_clock(self):
        """Moi lan goi strftime(id) tra mot giay moi, de id snapshot khong trung va thu tu xac dinh."""
        n = {"i": 0}
        real = sv_mod.time.strftime

        def fake(fmt, *a):
            if fmt == "%Y%m%d_%H%M%S":
                n["i"] += 1
                return f"20260101_{n['i'] // 3600:02d}{n['i'] // 60 % 60:02d}{n['i'] % 60:02d}"
            return real(fmt, *a)
        return mock.patch.object(sv_mod.time, "strftime", side_effect=fake)

    def _snap(self, vault, label, tag):
        # noi dung duy nhat moi lan -> moi snapshot co 1 object rieng de kiem GC
        (self.primary / "Saves" / "g.srm").write_bytes(f"{tag}".encode())
        r = vault.create_snapshot(label=label)
        self.assertTrue(r["success"], r)
        return r["snapshot_id"]

    def _referenced_shas(self, vault):
        shas = set()
        for s in vault.list_snapshots():
            mf = json.loads((Path(s["path"]) / "manifest.json").read_text(encoding="utf-8"))
            shas |= {f["sha256"] for f in mf["files"].values()}
        return shas

    def _object_names(self, vault):
        return {o.name for shard in vault.objects_dir.iterdir() for o in shard.iterdir()}

    def test_6_manual_15_auto_keeps_5_and_10_and_gc(self):
        vault = SaveVault(self.primary, None, self.ud)
        ids = {"manual": [], "auto": []}
        with self._fake_clock():
            for i in range(15):                       # xen ke: thu cong rai rac giua cac ban auto
                ids["auto"].append(self._snap(vault, "auto", f"auto-{i}"))
                if i % 2 == 0 and len(ids["manual"]) < 6:
                    ids["manual"].append(self._snap(vault, "manual", f"manual-{len(ids['manual'])}"))
            while len(ids["manual"]) < 6:
                ids["manual"].append(self._snap(vault, "manual", f"manual-{len(ids['manual'])}"))
        left = {s["id"] for s in vault.list_snapshots()}
        self.assertEqual(left & set(ids["manual"]), set(ids["manual"][1:]))      # 5 moi nhat
        self.assertEqual(left & set(ids["auto"]), set(ids["auto"][5:]))          # 10 moi nhat
        self.assertEqual(len(left), 15)
        # GC: object ton tai dung bang tap sha ma cac snapshot con giu tham chieu
        self.assertEqual(self._object_names(vault), self._referenced_shas(vault))
        self.assertEqual(len(self._object_names(vault)), 15)

    def test_journal_protected_snapshots_survive_in_both_groups(self):
        vault = SaveVault(self.primary, None, self.ud)
        with self._fake_clock():
            first_manual = self._snap(vault, "manual", "m0")
            first_auto = self._snap(vault, "auto", "a0")
            vault.journal_file.write_text(json.dumps({
                "status": "in_progress", "snapshot_id": first_manual, "pre_restore_id": first_auto,
                "planned_files": [], "restored_files": []}), encoding="utf-8")
            for i in range(1, 8):
                self._snap(vault, "manual", f"m{i}")
            for i in range(1, 14):
                self._snap(vault, "auto", f"a{i}")
        left = {s["id"] for s in vault.list_snapshots()}
        self.assertIn(first_manual, left)
        self.assertIn(first_auto, left)
        # object cua ban duoc bao ve van con de rollback duoc
        for sid in (first_manual, first_auto):
            self.assertTrue(vault.verify_snapshot(sid)["ok"], sid)

    def test_pre_restore_counts_as_manual_group(self):
        vault = SaveVault(self.primary, None, self.ud)
        with self._fake_clock():
            pre = self._snap(vault, "pre_restore", "p0")
            for i in range(14):                       # nhieu auto khong duoc dung toi pre_restore
                self._snap(vault, "auto", f"a{i}")
            self.assertIn(pre, {s["id"] for s in vault.list_snapshots()})
            for i in range(5):                        # 5 ban thu cong moi hon day pre_restore ra
                self._snap(vault, "manual", f"m{i}")
        self.assertNotIn(pre, {s["id"] for s in vault.list_snapshots()})

    def test_label_from_manifest_else_from_id_incl_dedupe_suffix(self):
        self.assertEqual(snapshot_label("snap_20260101_000001_auto"), "auto")
        self.assertEqual(snapshot_label("snap_20260101_000001_auto_2"), "auto_2")
        self.assertEqual(snapshot_label("snap_20260101_000001_pre_restore"), "pre_restore")
        self.assertEqual(snapshot_label("ten_khong_dung_dinh_dang"), "")          # khong suy duoc -> thu cong
        self.assertEqual(snapshot_label("snap_20260101_000001_manual", {"label": "auto"}), "auto")

    def test_old_manifest_without_label_is_grouped_by_id(self):
        vault = SaveVault(self.primary, None, self.ud)
        with self._fake_clock():
            for i in range(12):
                self._snap(vault, "auto", f"a{i}")
        # gia lap snapshot cu: go truong label khoi manifest
        for s in vault.list_snapshots():
            mp = Path(s["path"]) / "manifest.json"
            mf = json.loads(mp.read_text(encoding="utf-8"))
            mf.pop("label")
            mp.write_text(json.dumps(mf), encoding="utf-8")
        self.assertTrue(all(s["label"] == "auto" for s in vault.list_snapshots()))
        with self._fake_clock():
            self._snap(vault, "manual", "m0")
        snaps = vault.list_snapshots()
        self.assertEqual(len([s for s in snaps if s["label"].startswith("auto")]), 10)
        self.assertEqual(len([s for s in snaps if not s["label"].startswith("auto")]), 1)

    def test_defaults_match_what_agent_uses(self):
        vault = SaveVault(self.primary, None, self.ud)
        self.assertEqual((vault.max_retention, vault.auto_retention), (5, 10))


class StandaloneEmulatorPaths(Base):
    def _w(self, rel, data=b"x"):
        p = self.primary / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def test_mlp1_userdata_save_files_found_noise_excluded(self):
        self._w(".userdata/mlp1/flycast/vmu_save_A1.bin", b"v1")
        self._w(".userdata/mlp1/flycast/cache/vmu_save_B1.bin", b"noise")
        self._w(".userdata/mlp1/flycast/config/emu.cfg", b"noise")
        self._w(".userdata/mlp1/flycast/logs/flycast.log", b"noise")
        self._w(".userdata/mlp1/flycast/screenshots/s.png", b"noise")
        self._w(".userdata/mlp1/flycast/dc_boot.bin", b"bios-not-save")
        self._w(".userdata/mlp1/mupen64plus/save/game.eep", b"e")
        self._w(".userdata/mlp1/mupen64plus/save/game.st0", b"s")
        self._w(".userdata/mlp1/mupen64plus/mupen64plus.cfg", b"noise")
        self._w(".userdata/mlp1/mupen64plus/cache/c.srm", b"noise")
        self._w(".userdata/flycast/vmu_save_A2.bin", b"legacy")        # duong cu van duoc do
        self._w(".userdata/mlp2/yabasanshiro/backup.bin", b"y")       # PLATFORM khac mlp1
        self._w(".userdata/mlp1/drastic/backup/game.dsv", b"d")
        self._w(".userdata/mlp1/ppsspp/PSP/SAVEDATA/ULUS1/DATA.BIN", b"p")
        self._w(".userdata/mlp1/ppsspp/PSP/SAVEDATA/ULUS1/PARAM.SFO", b"p")
        self._w(".userdata/mlp1/ppsspp/PSP/SYSTEM/ppsspp.ini", b"noise")
        self._w("Saves/Flycast/old.bin", b"saves-dir")                  # thu muc Saves/ cu: lay het
        vault = SaveVault(self.primary, None, self.ud)
        files = set(vault.build_current_manifest()["files"])
        for want in (".userdata/mlp1/flycast/vmu_save_A1.bin", ".userdata/mlp1/mupen64plus/save/game.eep",
                     ".userdata/mlp1/mupen64plus/save/game.st0", ".userdata/flycast/vmu_save_A2.bin",
                     ".userdata/mlp2/yabasanshiro/backup.bin", ".userdata/mlp1/drastic/backup/game.dsv",
                     ".userdata/mlp1/ppsspp/PSP/SAVEDATA/ULUS1/DATA.BIN",
                     ".userdata/mlp1/ppsspp/PSP/SAVEDATA/ULUS1/PARAM.SFO", "Saves/Flycast/old.bin"):
            self.assertIn(want, files)
        for bad in (".userdata/mlp1/flycast/cache/vmu_save_B1.bin", ".userdata/mlp1/flycast/config/emu.cfg",
                    ".userdata/mlp1/flycast/logs/flycast.log", ".userdata/mlp1/flycast/screenshots/s.png",
                    ".userdata/mlp1/flycast/dc_boot.bin", ".userdata/mlp1/mupen64plus/mupen64plus.cfg",
                    ".userdata/mlp1/mupen64plus/cache/c.srm", ".userdata/mlp1/ppsspp/PSP/SYSTEM/ppsspp.ini"):
            self.assertNotIn(bad, files)

    def test_snapshot_and_restore_roundtrip_of_mlp1_save(self):
        self._w(".userdata/mlp1/flycast/vmu_save_A1.bin", b"original")
        vault = SaveVault(self.primary, None, self.ud)
        snap = vault.create_snapshot(label="manual")
        self.assertTrue(snap["success"], snap)
        (self.primary / ".userdata/mlp1/flycast/vmu_save_A1.bin").write_bytes(b"corrupted")
        res = vault.restore_snapshot(snap["snapshot_id"])
        self.assertTrue(res["success"], res)
        self.assertEqual((self.primary / ".userdata/mlp1/flycast/vmu_save_A1.bin").read_bytes(), b"original")

    def test_missing_userdata_is_fine(self):
        self.assertEqual(SaveVault(self.primary, None, self.ud).build_current_manifest()["total_files"], 0)


class OptionalBios(Base):
    def _audit(self, roms):
        for rel in roms:
            p = self.primary / "Roms" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"rom")
        for d in ("Images", "States", "BIOS"):
            (self.primary / d).mkdir(exist_ok=True)
        doc = LibraryDoctor(self.primary, None, CATALOG, self.ud)
        return doc, doc.audit(InventoryEngine(self.primary, None).scan_all(), {})

    def test_gba_nds_catalog_marks_bios_optional_not_required(self):
        doc = LibraryDoctor(self.primary, None, CATALOG, self.ud)
        gba, nds = doc.systems_catalog["GBA"], doc.systems_catalog["NDS"]
        self.assertEqual(gba["bios"], [])
        self.assertEqual(gba["bios_optional"], ["gba_bios.bin"])
        self.assertEqual(nds["bios"], [])
        self.assertEqual(nds["bios_optional"], ["bios7.bin", "bios9.bin", "firmware.bin"])

    def test_doctor_missing_optional_bios_is_info_and_status_stays_green(self):
        _, rep = self._audit(["GBA/game.gba", "NDS/game.nds"])
        by_id = {i["id"]: i for i in rep["issues"]}
        for sysid in ("GBA", "NDS"):
            self.assertNotIn(f"MISSING_BIOS_{sysid}", by_id)
            # GBA/NDS có BIOS giả lập (mGBA, DraStic): một ghi chú BIOS_HLE thay cho MISSING_BIOS_OPTIONAL
            self.assertNotIn(f"MISSING_BIOS_OPTIONAL_{sysid}", by_id)
            issue = by_id[f"BIOS_HLE_{sysid}"]
            self.assertEqual(issue["severity"], "info")
            self.assertIn("giả lập", issue["title"])
            self.assertTrue(issue["optional"])
        self.assertEqual(rep["severity_counts"]["warning"], 0)
        self.assertEqual(rep["status"], "green")

    def test_doctor_no_issue_when_optional_bios_present(self):
        (self.primary / "BIOS").mkdir(exist_ok=True)
        (self.primary / "BIOS" / "gba_bios.bin").write_bytes(b"b")
        _, rep = self._audit(["GBA/game.gba"])
        self.assertFalse([i for i in rep["issues"] if "BIOS" in i["id"] and "GBA" in i["id"]])

    def test_doctor_required_bios_still_warning(self):
        _, rep = self._audit(["FDS/game.fds"])
        issue = {i["id"]: i for i in rep["issues"]}["MISSING_BIOS_FDS"]
        self.assertEqual(issue["severity"], "warning")
        self.assertEqual(rep["status"], "yellow")

    def test_doctor_bios_with_emulated_fallback_is_a_note_not_a_warning(self):
        # Flycast tự dùng BIOS giả lập (reios) khi thiếu dc_boot.bin (log trên máy thật): không phải "không chạy được"
        _, rep = self._audit(["DC/game.chd"])
        ids = {i["id"]: i for i in rep["issues"]}
        self.assertNotIn("MISSING_BIOS_DC", ids)
        self.assertEqual(ids["BIOS_HLE_DC"]["severity"], "info")
        self.assertIn("reios", ids["BIOS_HLE_DC"]["description"])
        self.assertIn("Nhập game", ids["BIOS_HLE_DC"]["guide"])

    def test_bios_check_flags_optional_but_still_computes_status(self):
        doc = LibraryDoctor(self.primary, None, CATALOG, self.ud)
        cat = dict(doc.systems_catalog)
        (self.primary / "BIOS").mkdir(exist_ok=True)
        out = BIOS.check_bios(self.primary, None, cat, ["GBA", "DC", "FDS"])
        rows = {s["system"]: {r["name"]: r for r in s["required"]} for s in out["systems"]}
        self.assertFalse(rows["FDS"]["disksys.rom"]["optional"])
        self.assertTrue(rows["DC"]["dc_boot.bin"]["optional"], "DC có BIOS giả lập")
        self.assertIn("BIOS/dc/", rows["DC"]["dc_boot.bin"]["note"])
        gba = rows["GBA"]["gba_bios.bin"]
        self.assertTrue(gba["optional"])
        self.assertEqual(gba["status"], "missing")
        self.assertIn("không bắt buộc", gba["note"])
        # co file nhung sai hash -> status van duoc tinh, van gan optional
        (self.primary / "BIOS" / "gba_bios.bin").write_bytes(b"not the real bios")
        out = BIOS.check_bios(self.primary, None, cat, ["GBA"])
        row = out["systems"][0]["required"][0]
        self.assertEqual((row["status"], row["optional"]), ("wrong_hash", True))


@unittest.skipUnless(shutil.which("sh"), "can co sh")
class BuildScript(Base):
    def test_build_copies_bios_hashes_and_all_engine_modules(self):
        proj = self.tmp / "proj"
        shutil.copytree(ROOT / "engine", proj / "engine", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "pak" / "Apps", proj / "pak" / "Apps")
        shutil.copy(ROOT / "pak" / "build.sh", proj / "pak" / "build.sh")
        shutil.copy(CATALOG, proj / "systems.json")
        shutil.copy(ROOT / "bios_hashes.json", proj / "bios_hashes.json")
        r = subprocess.run(["sh", str(proj / "pak" / "build.sh")], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = proj / "pak" / "build" / "LoongMate.pak"
        self.assertEqual((out / "res" / "bios_hashes.json").read_bytes(), (ROOT / "bios_hashes.json").read_bytes())
        self.assertEqual((out / "res" / "systems.json").read_bytes(), CATALOG.read_bytes())
        for f in (ROOT / "engine").glob("*.py"):
            self.assertTrue((out / "engine" / f.name).is_file(), f.name)
        # bios.py trong pak tim duoc file hash o res/ (engine/ -> ../res/bios_hashes.json)
        code = "import sys; sys.path.insert(0, %r); from engine import bios; print(bios.find_hashes_file())" % str(out)
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertTrue(p.stdout.strip().replace("\\", "/").endswith("res/bios_hashes.json"), p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
