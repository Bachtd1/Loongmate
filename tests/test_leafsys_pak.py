"""Hệ máy do pak cài thêm (Pak Rat: J2ME.pak khai báo provides.systems trong pak.json). Leaf ghép chúng vào danh mục;
LoongMate cũng phải biết, nếu không Library Doctor báo nhầm Roms/J2ME là thư mục lạ và Nhập game không nhận .jar.
pak.json lấy nguyên từ máy thật (Leaf 0.13.0, J2ME.pak 0.1.0)."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import importer as IM  # noqa: E402
from engine import leafsys  # noqa: E402
from engine.doctor import LibraryDoctor  # noqa: E402
from engine.inventory import InventoryEngine  # noqa: E402

J2ME_PAK = {
    "name": "J2ME", "platform": "mlp1", "pak_version": "0.1.0", "min_leaf_version": "0.13.0",
    "provides": {"schema": 1, "systems": [{
        "id": "J2ME", "name": "J2ME", "patterns": ["J2ME"], "extensions": ["jar", "kjx"], "archive_extensions": [],
        "archive_mode": "none", "playlist_extensions": [], "m3u_generation": "none", "rom_root": "Roms/J2ME",
        "image_root": "Images/J2ME", "default_core": "freej2me_plus", "alternate_cores": []}],
        "system_extensions": [], "cores": [{"id": "freej2me_plus", "type": "retroarch"}]},
}
LEAF_SYSTEMS = {"systems": [{"id": "GBA", "patterns": ["GBA"], "extensions": ["gba"], "archive_extensions": ["zip"]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_pak_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        for c in (self.sd1, self.sd2):
            for d in ("Roms", "Images", "Saves", "States", "BIOS"):
                (c / d).mkdir(parents=True)
        self.w(self.sd1 / ".system/leaf/platforms/mlp1/defaults/systems.json", json.dumps(LEAF_SYSTEMS))

    def w(self, p, data="x"):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        return p

    def install_pak(self):
        self.w(self.sd1 / "Apps/mlp1/J2ME.pak/pak.json", json.dumps(J2ME_PAK))


class LeafSys(Base):
    def test_pak_systems_join_the_leaf_catalog(self):
        self.install_pak()
        leaf = leafsys.load(self.sd1)
        self.assertEqual(leafsys.system_of(leaf, "J2ME"), "J2ME")
        self.assertTrue(leafsys.shows_file(leaf, "J2ME", ".jar"))
        self.assertFalse(leafsys.shows_file(leaf, "J2ME", ".zip"))
        self.assertEqual(leaf["paks"]["J2ME"], "J2ME.pak")

    def test_folder_name_must_match_the_pak_pattern(self):
        self.install_pak()
        self.assertIsNone(leafsys.system_of(leafsys.load(self.sd1), "JAVA"))

    def test_broken_or_foreign_pak_json_is_ignored(self):
        self.install_pak()
        self.w(self.sd1 / "Apps/mlp1/Broken.pak/pak.json", "{not json")
        self.w(self.sd1 / "Apps/mlp1/Tool.pak/pak.json", json.dumps({"name": "Tool"}))
        self.assertEqual(leafsys.system_of(leafsys.load(self.sd1), "J2ME"), "J2ME")

    def test_builtin_system_wins_over_pak_with_same_folder(self):
        pak = json.loads(json.dumps(J2ME_PAK))
        pak["provides"]["systems"][0].update(id="FAKE", patterns=["GBA"])
        self.w(self.sd1 / "Apps/mlp1/Fake.pak/pak.json", json.dumps(pak))
        self.assertEqual(leafsys.system_of(leafsys.load(self.sd1), "GBA"), "GBA")


class Doctor(Base):
    def audit(self):
        doc = LibraryDoctor(self.sd1, self.sd2, ROOT / "systems.json", self.tmp / "ud")
        return doc.audit(InventoryEngine(self.sd1, self.sd2).scan_all(), {})

    def test_j2me_folder_is_not_reported_as_unknown_when_pak_installed(self):
        self.install_pak()
        self.w(self.sd2 / "Roms/J2ME/Ancient Empires 2.jar", "PK")
        ids = [i["id"] for i in self.audit()["issues"]]
        self.assertFalse([i for i in ids if "J2ME" in i and ("UNKNOWN" in i or "INVALID_EXT" in i)], ids)

    def test_without_the_pak_the_folder_is_still_unknown(self):
        self.w(self.sd2 / "Roms/J2ME/Ancient Empires 2.jar", "PK")
        ids = [i["id"] for i in self.audit()["issues"]]
        self.assertIn("UNKNOWN_SYSTEM_FOLDER_J2ME", ids)


class Importer(Base):
    def test_jar_is_recognised_and_installed_to_the_pak_folder(self):
        self.install_pak()
        imp = self.w(self.sd2 / "Import/Ancient Empires 2.jar", "PK\x03\x04 fake jar")
        cards = {"primary": self.sd1, "secondary": self.sd2}
        plan = IM.build_plan(None, cards, self.tmp / "ud")
        u = [u for u in plan["units"] if u.get("name", "").startswith("Ancient")][0]
        self.assertEqual((u["status"], u["system"], u["folder"]), ("ready", "J2ME", "J2ME"), u)
        self.assertTrue(imp.exists())

    def test_without_the_pak_jar_is_not_guessed(self):
        self.w(self.sd2 / "Import/Ancient Empires 2.jar", "PK\x03\x04 fake jar")
        plan = IM.build_plan(None, {"primary": self.sd1, "secondary": self.sd2}, self.tmp / "ud")
        u = [u for u in plan["units"] if u.get("name", "").startswith("Ancient")][0]
        self.assertNotEqual(u.get("system"), "J2ME")


if __name__ == "__main__":
    unittest.main()
