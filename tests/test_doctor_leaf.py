"""Library Doctor dùng danh mục hệ máy của chính Leaf: game Leaf không hiện (CPS1 ngoài ARCADE, .gbc trong GB) có cách sửa
có hoàn tác; ảnh bìa lệch tên ghép lại; file hệ thống của emulator không bị gọi là save mồ côi; ghi chú được gom nhóm."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import batchfix as BF  # noqa: E402
from engine.doctor import LibraryDoctor  # noqa: E402
from engine.inventory import InventoryEngine  # noqa: E402

LEAF_SYSTEMS = {"systems": [
    {"id": "ARCADE", "patterns": ["ARCADE", "Arcade", "arcade"], "extensions": [], "archive_extensions": ["zip"]},
    {"id": "GB", "patterns": ["GB"], "extensions": ["gb"], "archive_extensions": ["zip", "7z"]},
    {"id": "GBC", "patterns": ["GBC"], "extensions": ["gbc"], "archive_extensions": ["zip", "7z"]},
    {"id": "SFC", "patterns": ["SNES", "SFC"], "extensions": ["sfc", "smc"], "archive_extensions": ["zip"]},
    {"id": "GBA", "patterns": ["GBA"], "extensions": ["gba"], "archive_extensions": ["zip"]},
]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_leafdoc_"))
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        self.ud = self.tmp / "ud" / "LoongMate"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir(parents=True)
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def leaf(self):
        self.w(self.sd1 / ".system/leaf/platforms/mlp1/defaults/systems.json", json.dumps(LEAF_SYSTEMS).encode())

    def w(self, p: Path, data: bytes = b"x") -> Path:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def audit(self):
        self.doc = LibraryDoctor(self.sd1, self.sd2, ROOT / "systems.json", self.ud)
        self.inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        return self.doc.audit(self.inv, {})

    def ids(self, rep):
        return {i["id"]: i for i in rep["issues"]}


class LeafVisibility(Base):
    def test_cps1_hidden_from_leaf_is_a_warning_with_reversible_merge(self):
        self.leaf()
        self.w(self.sd1 / "Roms/CPS1/1941.zip", b"ROM1941")
        self.w(self.sd1 / "Images/CPS1/1941.png", b"IMG")
        rep = self.audit()
        iss = self.ids(rep)["LEGACY_SYSTEM_FOLDER_CPS1"]
        self.assertEqual(iss["severity"], "warning")
        self.assertIn("không hiện 1 game", iss["title"])
        plan = BF.plan_batch_fix(self.inv, rep, self.sd1, self.sd2, self.doc.systems_catalog)
        legacy = [s for s in plan["steps"] if s["kind"] == "legacy_folder"]
        self.assertEqual({s["data"]["canonical"] for s in legacy}, {"ARCADE"})
        res = BF.execute_batch_fix(BF.stamp_plan(plan), self.ud, selected_kinds=["legacy_folder"])
        self.assertEqual(res["refused"], [])
        self.assertEqual((self.sd1 / "Roms/ARCADE/1941.zip").read_bytes(), b"ROM1941")
        self.assertTrue((self.sd1 / "Images/ARCADE/1941.png").is_file())
        BF.undo_batch_fix(self.ud / BF.JOURNAL_NAME)
        self.assertEqual((self.sd1 / "Roms/CPS1/1941.zip").read_bytes(), b"ROM1941")

    def test_without_leaf_catalog_cps1_is_unchanged(self):
        self.w(self.sd1 / "Roms/CPS1/1941.zip")
        self.assertNotIn("LEGACY_SYSTEM_FOLDER_CPS1", self.ids(self.audit()))

    def test_gbc_in_gb_folder_moves_with_its_art_and_undoes(self):
        self.leaf()
        self.w(self.sd2 / "Roms/GB/Pokemon Crystal.gbc", b"CRYSTAL")
        self.w(self.sd2 / "Images/GB/Pokemon Crystal.png", b"ART")
        self.w(self.sd2 / "Saves/Gambatte/Pokemon Crystal.srm", b"SAVE")
        rep = self.audit()
        iss = next(i for i in rep["issues"] if i["id"].startswith("INVALID_EXT_GB_"))
        self.assertEqual((iss["severity"], iss["move_to"]), ("warning", "GBC"))
        plan = BF.stamp_plan(BF.plan_batch_fix(self.inv, rep, self.sd1, self.sd2, self.doc.systems_catalog))
        res = BF.execute_batch_fix(plan, self.ud, selected_kinds=["move_misplaced"])
        self.assertEqual(res["refused"], [])
        self.assertEqual((self.sd2 / "Roms/GBC/Pokemon Crystal.gbc").read_bytes(), b"CRYSTAL")
        self.assertEqual((self.sd2 / "Images/GBC/Pokemon Crystal.png").read_bytes(), b"ART")
        self.assertEqual((self.sd2 / "Saves/Gambatte/Pokemon Crystal.srm").read_bytes(), b"SAVE", "save không đổi chỗ")
        BF.undo_batch_fix(self.ud / BF.JOURNAL_NAME)
        self.assertTrue((self.sd2 / "Roms/GB/Pokemon Crystal.gbc").is_file())
        self.assertTrue((self.sd2 / "Images/GB/Pokemon Crystal.png").is_file())

    def test_move_never_overwrites(self):
        self.leaf()
        self.w(self.sd1 / "Roms/GB/Same.gbc", b"A")
        self.w(self.sd1 / "Roms/GBC/Same.gbc", b"B")
        rep = self.audit()
        plan = BF.stamp_plan(BF.plan_batch_fix(self.inv, rep, self.sd1, self.sd2, self.doc.systems_catalog))
        res = BF.execute_batch_fix(plan, self.ud, selected_kinds=["move_misplaced"])
        self.assertEqual(len(res["refused"]), 1)
        self.assertEqual((self.sd1 / "Roms/GB/Same.gbc").read_bytes(), b"A")
        self.assertEqual((self.sd1 / "Roms/GBC/Same.gbc").read_bytes(), b"B")


class ArtAndSaves(Base):
    def test_renamed_art_relinked_to_rom_card_and_original_quarantined(self):
        self.w(self.sd2 / "Roms/SNES/Mega Man X (USA) (Rev 1).sfc")
        art = self.w(self.sd1 / "Images/SNES/Mega Man X (USA).png", b"PNGDATA")
        self.w(self.sd1 / "Images/SNES/Alien vs. Predator (USA).png")
        rep = self.audit()
        acts = [a for a in rep["fixable_actions"] if a["type"] == "relink_art"]
        self.assertEqual(len(acts), 1)
        r = self.doc.execute_fix(acts[0])
        self.assertTrue(r["success"], r)
        self.assertEqual((self.sd2 / "Images/SNES/Mega Man X (USA) (Rev 1).png").read_bytes(), b"PNGDATA")
        self.assertFalse(art.exists(), "bản lệch tên vào Quarantine (khôi phục được)")
        left = self.ids(self.audit())["ORPHAN_ART_SNES"]
        self.assertEqual(left["items"], ["Alien vs. Predator (USA).png"], "ảnh không có game: chỉ ghi chú, không tự xoá")

    def test_duplicate_art_when_game_already_has_art_is_quarantinable(self):
        self.w(self.sd2 / "Roms/SNES/Mega Man X (USA) (Rev 1).sfc")
        self.w(self.sd2 / "Images/SNES/Mega Man X (USA) (Rev 1).png")
        self.w(self.sd1 / "Images/SNES/Mega Man X (USA).png")
        acts = [a for a in self.audit()["fixable_actions"] if a["type"] == "quarantine_file"]
        self.assertEqual([Path(a["path"]).name for a in acts], ["Mega Man X (USA).png"])

    def test_emulator_system_files_are_not_orphan_saves_and_orphans_are_grouped(self):
        self.w(self.sd1 / "Roms/GBA/Hero.gba")
        for rel in ("Saves/PCSX-ReARMed/pcsx-card2.mcd", "Saves/MAME/mame/cfg/default.cfg", "Saves/Flycast/vmu_save_A1.bin",
                    "Saves/Flycast/xdg/flycast/dc_nvmem.bin", "States/mGBA/Hero.state99.png", "Saves/mGBA/Hero.gba.sav",
                    "Saves/mGBA/Hero.srm"):
            self.w(self.sd1 / rel)
        self.w(self.sd1 / "Saves/mGBA/Deleted Game.srm")
        self.w(self.sd1 / "States/mGBA/Deleted Game.state99")
        orphans = [i for i in self.audit()["issues"] if i["id"].startswith("ORPHAN_SAVE")]
        self.assertEqual([(i["id"], i["items"]) for i in orphans],
                         [("ORPHAN_SAVE_mGBA", ["Deleted Game.srm", "Deleted Game.state99"])])

    def test_missing_art_grouped_per_system(self):
        for n in ("A.gba", "B.gba", "C.gba"):
            self.w(self.sd1 / "Roms/GBA" / n)
        self.w(self.sd1 / "Images/GBA/A.png")
        miss = [i for i in self.audit()["issues"] if i["id"].startswith("MISSING_ART")]
        self.assertEqual([(i["id"], i["items"]) for i in miss], [("MISSING_ART_GBA", ["B.gba", "C.gba"])])
        self.assertIn("Scrape Missing Artwork", miss[0]["guide"])


if __name__ == "__main__":
    unittest.main()
