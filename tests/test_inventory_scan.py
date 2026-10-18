"""
Kiểm hành vi InventoryEngine.scan_all sau khi viết lại bằng os.scandir (thay Path.rglob + resolve()).

Chuẩn đối chiếu (_RglobOracle) là NGUYÊN VĂN hai hàm quét cũ dùng pathlib. Hai bản phải cho cùng tập entry
(so theo absolute_path vì thứ tự rglob không được hứa hẹn và đổi theo phiên bản Python).
Không có cache quét: đo trên Pocket 1 cho thấy 97% thời gian là chi phí pathlib, mtime thư mục FAT32 cũng
không đổi khi ghi đè file tại chỗ nên cache theo thư mục không bỏ được stat từng file.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import inventory as inv_mod
from engine.inventory import InventoryEngine, is_safe_relative_path


class _RglobOracle(InventoryEngine):
    """Bản quét cũ (git 48ebce9) giữ nguyên để đối chiếu."""

    def _scan_system_folder(self, section_dir, card_id, include_mtime, scan_errors, unsafe_paths):
        items: List[Dict[str, Any]] = []
        try:
            sys_dirs = list(section_dir.iterdir())
        except OSError as e:
            scan_errors.append({"path": str(section_dir), "reason": str(e)})
            return items
        for sys_dir in sys_dirs:
            try:
                if not sys_dir.is_dir() or sys_dir.name.startswith("."):
                    continue
                system_name = sys_dir.name
                entries = sys_dir.rglob("*")
            except OSError as e:
                scan_errors.append({"path": str(sys_dir), "reason": str(e)})
                continue
            try:
                for item in entries:
                    try:
                        if not item.is_file() or item.name.startswith("."):
                            continue
                        if not is_safe_relative_path(section_dir, item):
                            unsafe_paths.append({"path": str(item), "reason": "Nằm ngoài thư mục gốc cho phép (path traversal/symlink)"})
                            continue
                        rel = item.relative_to(section_dir)
                        stat = item.stat()
                        entry = {"system": system_name, "name": item.name, "stem": item.stem,
                                 "extension": item.suffix.lower(),
                                 "relative_path": str(rel).replace("\\", "/"),
                                 "absolute_path": str(item), "size": stat.st_size, "card_id": card_id}
                        if include_mtime:
                            entry["mtime"] = int(stat.st_mtime)
                        items.append(entry)
                    except OSError as e:
                        scan_errors.append({"path": str(item), "reason": str(e)})
            except OSError as e:
                scan_errors.append({"path": str(sys_dir), "reason": str(e)})
        return items

    def _scan_flat_folder(self, section_dir, card_id, include_stem_ext, scan_errors, unsafe_paths):
        items: List[Dict[str, Any]] = []
        try:
            entries = section_dir.rglob("*")
        except OSError as e:
            scan_errors.append({"path": str(section_dir), "reason": str(e)})
            return items
        try:
            for item in entries:
                try:
                    if not item.is_file() or item.name.startswith("."):
                        continue
                    if not is_safe_relative_path(section_dir, item):
                        unsafe_paths.append({"path": str(item), "reason": "Nằm ngoài thư mục gốc cho phép (path traversal/symlink)"})
                        continue
                    stat = item.stat()
                    rel = item.relative_to(section_dir)
                    entry = {"name": item.name, "relative_path": str(rel).replace("\\", "/"),
                             "absolute_path": str(item), "size": stat.st_size,
                             "mtime": int(stat.st_mtime), "card_id": card_id}
                    if include_stem_ext:
                        entry["stem"] = item.stem
                        entry["extension"] = item.suffix.lower()
                    items.append(entry)
                except OSError as e:
                    scan_errors.append({"path": str(item), "reason": str(e)})
        except OSError as e:
            scan_errors.append({"path": str(section_dir), "reason": str(e)})
        return items


def _norm(scan: Dict[str, Any]) -> Dict[str, Any]:
    """Sắp mọi danh sách theo khoá ổn định để bỏ khác biệt thứ tự."""
    def key(d):
        return d.get("absolute_path") or d.get("path") or ""
    out = {}
    for k, v in scan.items():
        if isinstance(v, list):
            out[k] = sorted(v, key=key) if v and isinstance(v[0], dict) else v
        elif isinstance(v, dict):
            out[k] = _norm(v)
        else:
            out[k] = v
    return out


def _w(path: Path, data: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _build_card(root: Path) -> None:
    _w(root / "Roms" / "SNES" / "Game A.sfc", b"a" * 10)
    _w(root / "Roms" / "SNES" / "Game.v1.2.SFC", b"b" * 11)           # nhiều dấu chấm, đuôi hoa
    _w(root / "Roms" / "SNES" / "noext", b"c")                        # không đuôi
    _w(root / "Roms" / "SNES" / ".hidden.sfc", b"h")                  # file ẩn -> bỏ
    _w(root / "Roms" / "SNES" / "multi" / "disc" / "deep.cue", b"d")  # lồng sâu
    _w(root / "Roms" / "SNES" / "multi" / "disc" / "deep.bin", b"dd")
    _w(root / "Roms" / "SNES" / ".hiddendir" / "inner.sfc", b"i")     # thư mục ẩn con: rglob vẫn đi vào
    _w(root / "Roms" / "PSX" / "g.cue", b"1")
    _w(root / "Roms" / ".hidsys" / "x.rom", b"1")                     # thư mục hệ thống ẩn -> bỏ
    _w(root / "Roms" / "loose.rom", b"1")                             # file lẻ ở Roms/ -> không thuộc hệ thống nào
    (root / "Roms" / "EMPTY").mkdir()
    _w(root / "Images" / "SNES" / "Game A.png", b"p")
    _w(root / "Images" / "SNES" / "sub" / "z.png", b"p")
    _w(root / "Saves" / "Core" / "slot" / "a.srm", b"s")
    _w(root / "Saves" / "top.srm", b"s")
    _w(root / "States" / "Core" / "a.state1", b"s")
    _w(root / "BIOS" / "bios.bin", b"b")
    _w(root / "BIOS" / ".dot", b"b")


def _symlinks_supported(tmp: Path) -> bool:
    probe = tmp / "_probe"
    try:
        os.symlink(str(tmp), str(probe), target_is_directory=True)
        os.unlink(str(probe))
        return True
    except (OSError, NotImplementedError):
        return False


class InventoryScanTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        self.a = self.tmp / "sd1"
        self.b = self.tmp / "sd2"
        _build_card(self.a)
        _build_card(self.b)
        _w(self.b / "Roms" / "GBA" / "only_b.gba", b"q" * 5)

    def tearDown(self):
        self._td.cleanup()

    def _both(self, secondary=True):
        sec = self.b if secondary else None
        return InventoryEngine(self.a, sec).scan_all(), _RglobOracle(self.a, sec).scan_all()

    def assertSameAsOracle(self, secondary=True):
        new, old = self._both(secondary)
        self.assertEqual(_norm(new), _norm(old))
        return new

    # ---- hành vi tuyệt đối (không chỉ "giống bản cũ")
    def test_expected_entries(self):
        new = InventoryEngine(self.a).scan_all()
        roms = {r["relative_path"]: r for r in new["primary"]["roms"]}
        self.assertEqual(set(roms), {
            "SNES/Game A.sfc", "SNES/Game.v1.2.SFC", "SNES/noext",
            "SNES/multi/disc/deep.cue", "SNES/multi/disc/deep.bin", "SNES/.hiddendir/inner.sfc",
            "PSX/g.cue"})
        r = roms["SNES/Game.v1.2.SFC"]
        self.assertEqual((r["system"], r["stem"], r["extension"], r["size"], r["name"], r["card_id"]),
                         ("SNES", "Game.v1.2", ".sfc", 11, "Game.v1.2.SFC", "primary"))
        self.assertEqual(r["absolute_path"], str(self.a / "Roms" / "SNES" / "Game.v1.2.SFC"))
        self.assertIsInstance(r["mtime"], int)
        self.assertEqual(roms["SNES/noext"]["extension"], "")
        self.assertEqual(new["total_roms"], 7)
        self.assertEqual({s["relative_path"] for s in new["primary"]["saves"]}, {"Core/slot/a.srm", "top.srm"})
        self.assertEqual({s["relative_path"] for s in new["primary"]["bios"]}, {"bios.bin"})
        self.assertNotIn("stem", new["primary"]["bios"][0])
        self.assertEqual({i["relative_path"] for i in new["primary"]["images"]}, {"SNES/Game A.png", "SNES/sub/z.png"})
        self.assertNotIn("mtime", new["primary"]["images"][0])
        self.assertIsNone(new["secondary"])

    def test_same_as_oracle_plain_tree(self):
        self.assertSameAsOracle()

    def test_odd_file_names_match_oracle(self):
        for n in ("trail.", "dots...", "a.b.", "x..y", "UPPER.ZIP", "tar.gz.bak", "sp ace.n64"):
            _w(self.a / "Roms" / "SNES" / n, b"1")
        new = self.assertSameAsOracle()
        by = {r["name"]: r for r in new["primary"]["roms"]}
        self.assertEqual((by["UPPER.ZIP"]["stem"], by["UPPER.ZIP"]["extension"]), ("UPPER", ".zip"))
        self.assertEqual((by["tar.gz.bak"]["stem"], by["tar.gz.bak"]["extension"]), ("tar.gz", ".bak"))

    def test_same_as_oracle_after_changes(self):
        snes = self.a / "Roms" / "SNES"
        scenarios = [
            lambda: _w(snes / "new.sfc", b"n"),                      # thêm
            lambda: os.remove(snes / "Game A.sfc"),                  # xoá
            lambda: _w(snes / "Game A.sfc", b"Z" * 10),              # tạo lại
            lambda: os.rename(snes / "Game A.sfc", snes / "Renamed.sfc"),
            lambda: _w(snes / "Renamed.sfc", b"Y" * 99),             # đổi size
            lambda: _w(self.a / "Roms" / "NEWSYS" / "x" / "y.rom", b"1"),   # thư mục mới
        ]
        for i, change in enumerate(scenarios):
            with self.subTest(i=i):
                change()
                self.assertSameAsOracle()

    def test_size_change_is_visible(self):
        f = self.a / "Roms" / "SNES" / "Game A.sfc"
        before = {r["name"]: r["size"] for r in InventoryEngine(self.a).scan_all()["primary"]["roms"]}
        _w(f, b"Z" * 77)
        after = {r["name"]: r["size"] for r in InventoryEngine(self.a).scan_all()["primary"]["roms"]}
        self.assertEqual((before["Game A.sfc"], after["Game A.sfc"]), (10, 77))

    def test_card_removed_or_missing_dirs(self):
        shutil.rmtree(self.b)                      # thẻ 2 rút ra
        new, old = self._both(True)
        self.assertEqual(_norm(new), _norm(old))
        self.assertEqual(new["secondary"]["roms"], [])
        shutil.rmtree(self.a / "Saves")
        self.assertSameAsOracle(False)

    def test_scan_does_not_call_resolve_for_plain_files(self):
        # Đây là lý do nhanh: cây thường không đi qua is_safe_relative_path/resolve().
        with mock.patch.object(inv_mod, "is_safe_relative_path", wraps=is_safe_relative_path) as spy:
            InventoryEngine(self.a, self.b).scan_all()
        self.assertEqual(spy.call_count, 0)

    def test_unreadable_subdir_is_isolated(self):
        orig = os.scandir

        def flaky(path, *a, **k):
            if str(path).endswith("disc"):
                raise OSError("giả lập thẻ rút")
            return orig(path, *a, **k)
        with mock.patch.object(inv_mod.os, "scandir", flaky):
            new = InventoryEngine(self.a).scan_all()
        rels = {r["relative_path"] for r in new["primary"]["roms"]}
        self.assertIn("SNES/Game A.sfc", rels)
        self.assertNotIn("SNES/multi/disc/deep.cue", rels)
        self.assertEqual(len(new["scan_errors"]), 1)
        self.assertTrue(new["scan_errors"][0]["path"].endswith("disc"))
        self.assertIn("giả lập", new["scan_errors"][0]["reason"])


class InventorySymlinkTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(self._td.name).resolve()
        if not _symlinks_supported(self.tmp):
            self.skipTest("hệ điều hành không cho tạo symlink")
        self.a = self.tmp / "sd1"
        _build_card(self.a)
        self.outside = self.tmp / "outside"
        _w(self.outside / "secret.rom", b"s")
        _w(self.outside / "dirfile.rom", b"s")

    def test_symlinks_match_oracle_and_are_flagged(self):
        snes = self.a / "Roms" / "SNES"
        os.symlink(str(self.outside / "secret.rom"), str(snes / "evil.rom"))              # file ra ngoài
        os.symlink(str(snes / "Game A.sfc"), str(snes / "alias.sfc"))                     # file symlink trong gốc
        os.symlink(str(self.outside), str(snes / "outdir"), target_is_directory=True)     # thư mục symlink ra ngoài
        os.symlink(str(snes / "nonexistent"), str(snes / "broken.rom"))                   # symlink gãy
        os.symlink(str(self.outside), str(self.a / "Roms" / "LINKSYS"), target_is_directory=True)  # hệ thống là symlink
        os.symlink(str(self.outside / "dirfile.rom"), str(self.a / "Saves" / "ev.srm"))
        with mock.patch.object(inv_mod, "is_safe_relative_path", wraps=is_safe_relative_path) as spy:
            new = InventoryEngine(self.a).scan_all()
        old = _RglobOracle(self.a).scan_all()
        self.assertEqual(_norm(new), _norm(old))
        unsafe = {Path(u["path"]).name for u in new["unsafe_paths"]}
        self.assertIn("evil.rom", unsafe)
        self.assertIn("secret.rom", unsafe)     # qua LINKSYS (symlink hệ thống)
        self.assertIn("ev.srm", unsafe)
        rels = {r["relative_path"] for r in new["primary"]["roms"]}
        self.assertIn("SNES/alias.sfc", rels)
        self.assertNotIn("SNES/outdir", rels)
        self.assertNotIn("SNES/broken.rom", rels)
        self.assertGreater(spy.call_count, 0)   # đường chậm vẫn được dùng cho symlink


if __name__ == "__main__":
    unittest.main()
