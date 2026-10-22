"""Local Importer (engine/importer.py): hành vi thật trên thẻ giả trong thư mục tạm, zip tạo ngay trong test.

Chứng minh test biết đỏ: đặt LOONGMATE_IMPORTER_PATH tới một bản importer.py cố tình làm hỏng (xem docs/NHAP-GAME.md
mục "Chạy test"), các test tương ứng phải đỏ. Test KHÔNG grep mã nguồn: chỉ chạy scan/plan/apply thật rồi nhìn cây thư mục.
"""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_override = os.environ.get("LOONGMATE_IMPORTER_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("importer_under_test", _override)
    IM = importlib.util.module_from_spec(_spec)
    sys.modules["importer_under_test"] = IM
    _spec.loader.exec_module(IM)
else:
    import engine.importer as IM

MB = 1024 * 1024


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------- ROM tối thiểu theo tài liệu header
def gba_rom(title=b"HOMEBREW", size=0x400, seed=0):
    d = bytearray(size)
    d[0:4] = b"\x2e\x00\x00\xea"
    d[4:0xA0] = IM.GBA_LOGO
    d[0xA0:0xA0 + len(title)] = title
    d[0xB2] = 0x96
    d[0xBD] = (-(0x19 + sum(d[0xA0:0xBD]))) & 0xFF
    d[0x200] = seed
    return bytes(d)


def gb_rom(cgb=None, size=0x8000, seed=0):
    d = bytearray(size)
    d[0x104:0x134] = IM.GB_LOGO
    d[0x134:0x13A] = b"TESTGB"
    if cgb is not None:
        d[0x143] = cgb
    c = 0
    for b in d[0x134:0x14D]:
        c = (c - b - 1) & 0xFF
    d[0x14D] = c
    d[0x200] = seed
    return bytes(d)


def nes_rom(seed=0):
    return b"NES\x1a" + bytes([2, 1, 0, 0]) + bytes(8) + bytes([seed]) * 64


def snes_rom(size=0x10000, base=0x7FC0, seed=0, map_mode=0x20):
    d = bytearray(size)
    d[base:base + 21] = b"HOMEBREW SNES TEST   "
    d[base + 0x15] = map_mode
    chk = 0x1234
    d[base + 0x1C:base + 0x1E] = struct.pack("<H", chk ^ 0xFFFF)
    d[base + 0x1E:base + 0x20] = struct.pack("<H", chk)
    d[0x100] = seed
    return bytes(d)


def n64_rom(magic=b"\x80\x37\x12\x40", seed=0):
    return magic + bytes(0x1000 - 4) + bytes([seed])


def genesis_rom(console=b"SEGA MEGA DRIVE ", seed=0):
    d = bytearray(0x400)
    d[0x100:0x100 + len(console)] = console
    d[0x300] = seed
    return bytes(d)


def sms_rom(region_nibble=4, off=0x7FF0, seed=0):
    d = bytearray(0x8000 if off == 0x7FF0 else off + 16)
    d[off:off + 8] = b"TMR SEGA"
    d[off + 0x0F] = (region_nibble << 4) | 0x0C
    d[0x10] = seed
    return bytes(d)


def raw_sector(n, user: bytes, mode=1):
    """Sector CD thô 2352 byte: sync + header + dữ liệu người dùng."""
    s = bytearray(2352)
    s[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    s[15] = mode
    off = 16 if mode == 1 else 24
    s[off:off + len(user)] = user
    return bytes(s)


def psx_bin(seed=0):
    sectors = [raw_sector(i, b"") for i in range(16)]
    pvd = b"\x01CD001\x01\x00" + b"PLAYSTATION".ljust(32)
    sectors.append(raw_sector(16, pvd))
    d = bytearray(b"".join(sectors))
    d[0x24F8:0x24F8 + 14] = b"Sony Computer "
    return bytes(d) + bytes([seed]) * 2352


def saturn_bin(seed=0):
    return raw_sector(0, b"SEGA SEGASATURN " + b"x" * 16) + raw_sector(1, bytes([seed]) * 100)


def segacd_bin():
    return raw_sector(0, b"SEGADISCSYSTEM  " + b"x" * 16) + raw_sector(1, b"")


def mkzip(path: Path, entries, method=zipfile.ZIP_DEFLATED, attrs=None):
    """entries: {name: bytes}; attrs: {name: external_attr} (đặt symlink/device/exec bằng create_system=3)."""
    with zipfile.ZipFile(path, "w", method) as z:
        for name, data in entries.items():
            zi = zipfile.ZipInfo(name, (2024, 1, 1, 0, 0, 0))
            zi.compress_type = method
            if attrs and name in attrs:
                zi.create_system = 3
                zi.external_attr = attrs[name] << 16
            z.writestr(zi, data)
    return path


def tree(root: Path):
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = Path(dp) / f
            out[str(p.relative_to(root)).replace("\\", "/")] = sha(p.read_bytes())
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_import_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for c in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (c / s).mkdir(parents=True)
        self.ud.mkdir()
        self.cards = {"primary": self.sd1, "secondary": self.sd2}
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.free = {str(self.sd1): 900 * MB, str(self.sd2): 20 * 1024 * MB}
        p = mock.patch.object(IM, "_free", lambda root: self.free.get(str(root), 10 * 1024 * MB))
        p.start()
        self.addCleanup(p.stop)
        self.imp1, self.imp2 = self.sd1 / "Import", self.sd2 / "Import"
        self.imp1.mkdir()
        self.imp2.mkdir()

    def w(self, path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self, paths=None, **kw):
        return IM.scan(paths, self.cards, **kw)

    def plan(self, paths=None, pref="auto", **kw):
        return IM.build_plan(paths, self.cards, self.ud, card_pref=pref, **kw)

    def apply(self, plan=None, **kw):
        plan = plan or self.plan()
        return IM.apply_plan(plan, self.cards, self.ud, **kw)

    def unit(self, res, name=None):
        us = [u for u in res["units"] if name is None or u["name"] == name]
        self.assertEqual(len(us), 1, [u["name"] for u in res["units"]])
        return us[0]

    def outside(self, before):
        """Mọi file mới xuất hiện phải nằm trong Roms/ hoặc BIOS/ hoặc .loongmate-import/ hoặc Import/.done của thẻ."""
        after = tree(self.tmp)
        new = set(after) - set(before)
        bad = [p for p in new if not any(seg in "/" + p for seg in ("/Roms/", "/BIOS/", "/.loongmate-import/", "/Import/", "/ud/"))]
        return bad


# ======================================================================================
class TestClassify(Base):
    def cls(self, name, data):
        return IM.classify_bytes(name, data[:IM.HEAD_BYTES], IM.load_catalog())

    def test_headers_per_system(self):
        cases = [
            ("a.gba", gba_rom(), "GBA"), ("a.bin", gba_rom(), "GBA"),
            ("a.gb", gb_rom(), "GB"), ("a.gbc", gb_rom(cgb=0xC0), "GBC"), ("a.gb", gb_rom(cgb=0xC0), "GBC"),
            ("a.nes", nes_rom(), "NES"), ("a.rom", nes_rom(), "NES"),
            ("a.sfc", snes_rom(), "SNES"), ("a.smc", snes_rom(size=0x10000 + 0x200)[:0x10000], None),
            ("a.xyz", snes_rom(base=0xFFC0, size=0x20000, map_mode=0x21), "SNES"),
            ("a.z64", n64_rom(), "N64"), ("a.v64", n64_rom(b"\x37\x80\x40\x12"), "N64"),
            ("a.n64", n64_rom(b"\x40\x12\x37\x80"), "N64"), ("a.bin", n64_rom(), "N64"),
            ("a.bin", genesis_rom(), "GENESIS"), ("a.md", genesis_rom(b"SEGA GENESIS    "), "GENESIS"),
            ("a.sms", sms_rom(4), "MS"), ("a.gg", sms_rom(6), "GG"), ("a.bin", sms_rom(5), "GG"),
            ("a.bin", sms_rom(3, off=0x1FF0), "MS"),
            ("a.bin", psx_bin(), "PSX"), ("a.bin", saturn_bin(), "SATURN"), ("a.bin", segacd_bin(), "SEGACD"),
        ]
        for name, data, want in cases:
            if want is None:
                continue
            with self.subTest(name=name, want=want):
                r = self.cls(name, data)
                self.assertEqual((r["status"], r["system"]), ("ready", want), r)

    def test_evidence_cites_header(self):
        r = self.cls("x.gba", gba_rom())
        self.assertEqual(r["by"], "header")
        self.assertIn("0x96", r["evidence"])

    def test_gb_dual_flag_uses_extension_else_asks(self):
        d = gb_rom(cgb=0x80)
        self.assertEqual(self.cls("a.gb", d)["system"], "GB")
        self.assertEqual(self.cls("a.gbc", d)["system"], "GBC")
        r = self.cls("a.rom", d)
        self.assertEqual((r["status"], sorted(r["candidates"])), ("needs_choice", ["GB", "GBC"]))

    def test_32x_header_needs_choice_between_folders(self):
        r = self.cls("a.bin", genesis_rom(b"SEGA 32X        "))
        self.assertEqual((r["status"], sorted(r["candidates"])), ("needs_choice", ["32X", "MD32X"]))

    def test_unique_extension(self):
        r = self.cls("x.pce", b"\x00" * 100)
        self.assertEqual((r["status"], r["system"], r["by"]), ("ready", "PCE", "extension"))

    def test_ambiguous_bin_needs_choice(self):
        r = self.cls("x.bin", b"\x00" * 4096)
        self.assertEqual(r["status"], "needs_choice")
        self.assertEqual(r["system"], None)
        self.assertIn("GENESIS", r["candidates"])
        self.assertIn("ATARI2600", r["candidates"])

    def test_header_beats_extension_with_warning(self):
        r = self.cls("x.nes", gba_rom())
        self.assertEqual(r["system"], "GBA")
        self.assertTrue(any("nội dung" in w for w in r["warnings"]))

    def test_unknown_extension(self):
        self.assertEqual(self.cls("x.qqq", b"\x01" * 64)["status"], "unknown")

    def test_nds_header(self):
        d = bytearray(0x400)
        d[0xC0:0xC0 + 156] = IM.GBA_LOGO
        struct.pack_into("<H", d, 0x15C, IM._crc16_nds(bytes(d[0xC0:0xC0 + 156])))
        struct.pack_into("<H", d, 0x15E, IM._crc16_nds(bytes(d[:0x15E])))
        self.assertEqual(IM._crc16_nds(IM.GBA_LOGO), 0xCF56)     # hằng CF56h trong tài liệu NDS
        r = self.cls("x.bin", bytes(d))
        self.assertEqual((r["status"], r["system"]), ("ready", "NDS"))


# ======================================================================================
class TestScanSources(Base):
    def test_scan_reads_both_import_dirs_and_writes_nothing(self):
        self.w(self.imp1 / "a.gba", gba_rom())
        mkzip(self.imp2 / "b.zip", {"b.nes": nes_rom()})
        before = tree(self.tmp)
        r = self.scan()
        self.assertEqual({u["name"] for u in r["units"]}, {"a", "b"})
        self.assertEqual({u["card"] for u in r["units"]}, {"primary", "secondary"})
        self.assertEqual(tree(self.tmp), before)

    def test_scan_does_not_create_import_dir(self):
        shutil.rmtree(self.imp1)
        shutil.rmtree(self.imp2)
        r = self.scan()
        self.assertEqual(r["units"], [])
        self.assertFalse(self.imp1.exists())
        self.assertFalse(r["import_dirs"][0]["exists"])

    def test_ensure_import_dir_creates(self):
        shutil.rmtree(self.imp1)
        out = IM.ensure_import_dir(self.sd1)
        self.assertTrue(out["ok"])
        self.assertTrue(self.imp1.is_dir())

    def test_zip_single_rom_with_readme(self):
        mkzip(self.imp1 / "g.zip", {"Game.gba": gba_rom(), "README.txt": b"hi", "Game.sav": b"SAVE"})
        r = self.scan()
        u = self.unit(r)
        self.assertEqual((u["status"], u["system"], u["folder"]), ("ready", "GBA", "GBA"))
        self.assertEqual([f["dest_rel"] for f in u["files"]], ["Game.gba"])
        reasons = {s["reason"] for s in r["skipped"]}
        self.assertTrue(any("save" in x for x in reasons))

    def test_7z_and_rar_need_unpack_when_7zip_bundled(self):
        """.7z/.rar: có 7-Zip đi kèm pak -> "cần giải nén" (engine/archive.py, theo yêu cầu người dùng); quét vẫn chỉ đọc."""
        from engine import archive
        self.w(self.imp1 / "a.7z", b"7z\xbc\xaf\x27\x1c" + b"0" * 20)
        self.w(self.imp1 / "b.rar", b"Rar!" + b"0" * 20)
        with mock.patch.object(archive, "seven_zip", return_value="/fake/7zzs"):
            r = self.scan()
        self.assertEqual({u["status"] for u in r["units"]}, {"needs_unpack"})
        self.assertTrue((self.imp1 / "a.7z").exists() and (self.imp1 / "b.rar").exists())

    def test_7z_and_rar_unsupported_without_7zip(self):
        from engine import archive
        self.w(self.imp1 / "a.7z", b"7z\xbc\xaf\x27\x1c" + b"0" * 20)
        self.w(self.imp1 / "b.rar", b"Rar!" + b"0" * 20)
        with mock.patch.object(archive, "seven_zip", return_value=None):
            r = self.scan()
        self.assertEqual({u["status"] for u in r["units"]}, {"unsupported"})
        self.assertTrue(all("7-Zip" in u["reasons"][0] for u in r["units"]))

    def test_zip_multiple_games_each_classified(self):
        mkzip(self.imp1 / "pack.zip", {"a.gba": gba_rom(seed=1), "b.nes": nes_rom(), "c.gb": gb_rom()})
        r = self.scan()
        self.assertEqual({u["system"] for u in r["units"]}, {"GBA", "NES", "GB"})
        self.assertTrue(all("nhiều hệ" in " ".join(u["warnings"]) for u in r["units"]))

    def test_arcade_style_zip_asks_system_and_installs_whole_zip(self):
        mkzip(self.imp1 / "pacman.zip", {"pacman.6e": b"1" * 100, "pacman.6f": b"2" * 100, "82s123.7f": b"3" * 32})
        u = self.unit(self.scan())
        self.assertEqual((u["kind"], u["status"]), ("archive", "needs_choice"))
        self.assertIn("ARCADE", u["candidates"])
        self.assertIn("MAME", u["candidates"])
        plan = self.plan()
        res = self.apply(plan, choices={u["id"]: "ARCADE"})
        self.assertEqual(res["installed"], 1, res)
        self.assertTrue((self.sd2 / "Roms" / "ARCADE" / "pacman.zip").is_file())

    def test_custom_path_zip_and_dir(self):
        z = mkzip(self.tmp / "x.zip", {"x.gba": gba_rom()})
        d = self.tmp / "folder"
        self.w(d / "y.nes", nes_rom())
        r = self.scan([z, d])
        self.assertEqual({u["system"] for u in r["units"]}, {"GBA", "NES"})
        self.assertTrue(all(not u["in_import"] for u in r["units"]))

    def test_cue_bin_keeps_layout_flat_and_whole_set(self):
        cue = 'FILE "Game (Track 1).bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\nFILE "Game (Track 2).bin" BINARY\r\n  TRACK 02 AUDIO\r\n    INDEX 01 00:00:00\r\n'
        mkzip(self.imp1 / "g.zip", {"Game.cue": cue.encode(), "Game (Track 1).bin": psx_bin(), "Game (Track 2).bin": b"\x00" * 5000})
        u = self.unit(self.scan())
        self.assertEqual((u["kind"], u["status"], u["system"]), ("multi", "ready", "PSX"))
        self.assertEqual(sorted(f["dest_rel"] for f in u["files"]), ["Game (Track 1).bin", "Game (Track 2).bin", "Game.cue"])
        res = self.apply()
        self.assertEqual(res["installed"], 1, res)
        d = self.sd2 / "Roms" / "PSX"
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["Game (Track 1).bin", "Game (Track 2).bin", "Game.cue"])

    def test_m3u_with_subfolder_layout_kept(self):
        files = {"G.m3u": b"Disc1/G1.cue\nDisc2/G2.cue\n"}
        for i in (1, 2):
            files[f"Disc{i}/G{i}.cue"] = f'FILE "G{i}.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'.encode()
            files[f"Disc{i}/G{i}.bin"] = psx_bin(seed=i)
        mkzip(self.imp1 / "g.zip", files)
        r = self.scan()
        u = self.unit(r)
        self.assertEqual((u["kind"], u["system"]), ("multi", "PSX"))
        self.assertEqual(len(u["files"]), 5)
        res = self.apply()
        self.assertEqual(res["installed"], 1, res)
        base = self.sd2 / "Roms" / "PSX"
        self.assertTrue((base / "Disc2" / "G2.bin").is_file())
        self.assertTrue((base / "G.m3u").is_file())

    def test_cue_missing_track_blocked(self):
        mkzip(self.imp1 / "g.zip", {"G.cue": b'FILE "G.bin" BINARY\r\n', "other.txt": b"x"})
        u = self.unit(self.scan())
        self.assertEqual(u["status"], "blocked")
        self.assertIn("không có", " ".join(u["reasons"]))

    def test_cue_reference_escaping_blocked(self):
        mkzip(self.imp1 / "g.zip", {"G.cue": b'FILE "../secret.bin" BINARY\r\n', "secret.bin": b"x" * 10})
        u = self.unit(self.scan(), "G")
        self.assertEqual(u["status"], "blocked")
        self.assertIn("không hợp lệ", " ".join(u["reasons"]))

    def test_loose_cue_bin_in_import_grouped(self):
        self.w(self.imp1 / "G.cue", b'FILE "G.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n')
        self.w(self.imp1 / "G.bin", saturn_bin())
        self.w(self.imp1 / "H.gba", gba_rom())
        r = self.scan()
        self.assertEqual({u["name"] for u in r["units"]}, {"G", "H"})
        g = self.unit(r, "G")
        self.assertEqual((g["kind"], g["system"]), ("multi", "SATURN"))

    def test_cue_without_header_needs_choice(self):
        self.w(self.imp1 / "G.cue", b'FILE "G.bin" BINARY\r\n')
        self.w(self.imp1 / "G.bin", b"\x00" * 3000)
        u = self.unit(self.scan())
        self.assertEqual(u["status"], "needs_choice")
        self.assertIn("PSX", u["candidates"])

    def test_gdi_is_dreamcast(self):
        self.w(self.imp1 / "G.gdi", b'2\n1 0 4 2352 "track01.bin" 0\n2 600 0 2352 "track02.raw" 0\n')
        self.w(self.imp1 / "track01.bin", b"\x01" * 3000)
        self.w(self.imp1 / "track02.raw", b"\x02" * 3000)
        u = self.unit(self.scan())
        self.assertEqual((u["system"], u["kind"]), ("DC", "multi"))
        self.assertEqual(len(u["files"]), 3)


# ======================================================================================
class TestBios(Base):
    def test_bios_md5_match_proposed_separately(self):
        # BIOS giả: tạo file rồi đưa md5 của nó vào bios_hashes tạm
        data = b"FAKE-BIOS-" * 100
        h = {"gba_bios.bin": {"md5": [hashlib.md5(data).hexdigest()], "source": "t", "systems": ["GBA"]}}
        hp = self.tmp / "bh.json"
        hp.write_text(json.dumps(h))
        bios = IM.load_bios_md5(hp)
        self.w(self.imp1 / "SomeName.bin", data)
        r = self.scan(bios=bios)
        u = self.unit(r)
        self.assertEqual((u["kind"], u["status"]), ("bios", "ready"))
        self.assertEqual(u["bios"]["dest"], "BIOS/gba_bios.bin")
        plan = IM.build_plan(None, self.cards, self.ud, bios=bios)
        self.assertEqual(plan["units"][0]["action"], "bios_confirm")
        res = IM.apply_plan(plan, self.cards, self.ud, bios=bios)          # chưa xác nhận -> không cài
        self.assertEqual(res["installed"], 0)
        self.assertFalse((self.sd2 / "BIOS" / "gba_bios.bin").exists())
        res = IM.apply_plan(plan, self.cards, self.ud, bios=bios, bios_ok=[u["id"]])
        self.assertEqual(res["installed"], 1, res)
        self.assertEqual((self.sd2 / "BIOS" / "gba_bios.bin").read_bytes(), data)

    def test_bios_name_with_wrong_md5_not_a_game(self):
        h = {"gba_bios.bin": {"md5": ["0" * 32], "source": "t", "systems": ["GBA"]}}
        hp = self.tmp / "bh.json"
        hp.write_text(json.dumps(h))
        self.w(self.imp1 / "gba_bios.bin", b"not the real bios")
        u = self.unit(self.scan(bios=IM.load_bios_md5(hp)))
        self.assertEqual(u["status"], "blocked")
        self.assertIn("không coi là game", u["reasons"][0])

    def test_never_overwrite_existing_bios(self):
        data = b"REAL-BIOS" * 50
        h = {"b.bin": {"md5": [hashlib.md5(data).hexdigest()], "source": "t", "systems": ["GBA"]}}
        hp = self.tmp / "bh.json"
        hp.write_text(json.dumps(h))
        bios = IM.load_bios_md5(hp)
        self.w(self.sd2 / "BIOS" / "b.bin", b"user's other bios")
        self.w(self.imp1 / "x.bin", data)
        plan = IM.build_plan(None, self.cards, self.ud, bios=bios)
        self.assertEqual(plan["units"][0]["action"], "bios_conflict")
        res = IM.apply_plan(plan, self.cards, self.ud, bios=bios, bios_ok=["all"])
        self.assertEqual(res["installed"], 0)
        self.assertEqual((self.sd2 / "BIOS" / "b.bin").read_bytes(), b"user's other bios")

    def test_real_catalog_hashes_loaded(self):
        b = IM.load_bios_md5()
        self.assertIn("a860e8c0b6d573d191e4ec7db1b1e4f6", b["by_md5"])


# ======================================================================================
class TestArchiveSafety(Base):
    """Mọi vi phạm -> blocked, và KHÔNG có file nào xuất hiện ngoài thư mục được phép."""

    def attack(self, zippath, limits=None):
        before = tree(self.tmp)
        kw = {"limits": limits} if limits else {}
        r = self.scan(**kw)
        u = self.unit(r)
        self.assertEqual(u["status"], "blocked", u)
        plan = self.plan(**kw)
        res = IM.apply_plan(plan, self.cards, self.ud, **kw)
        self.assertEqual(res["installed"], 0, res)
        self.assertEqual(self.outside(before), [], "file lọt ra ngoài vùng cho phép")
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.assertEqual(list((self.sd1 / "Roms").rglob("*")), [])
        return u

    def test_path_traversal(self):
        mkzip(self.imp1 / "evil.zip", {"../../outside.gba": gba_rom(), "ok.gba": gba_rom(seed=1)})
        u = self.attack(self.imp1 / "evil.zip")
        self.assertIn("path traversal", " ".join(u["reasons"]))
        self.assertFalse((self.tmp / "outside.gba").exists())
        self.assertFalse((self.tmp.parent / "outside.gba").exists())

    def test_traversal_into_saves(self):
        self.w(self.sd2 / "Saves" / "g.srm", b"PRECIOUS")
        mkzip(self.imp1 / "evil.zip", {"..\\Saves\\g.srm": b"OVERWRITE", "ok.gba": gba_rom()})
        self.attack(self.imp1 / "evil.zip")
        self.assertEqual((self.sd2 / "Saves" / "g.srm").read_bytes(), b"PRECIOUS")

    def test_absolute_path(self):
        mkzip(self.imp1 / "evil.zip", {"/etc/passwd.gba": gba_rom()})
        u = self.attack(self.imp1 / "evil.zip")
        self.assertIn("tuyệt đối", " ".join(u["reasons"]))

    def test_windows_drive_path(self):
        mkzip(self.imp1 / "evil.zip", {"C:/x/y.gba": gba_rom()})
        self.attack(self.imp1 / "evil.zip")

    def test_symlink_in_zip(self):
        mkzip(self.imp1 / "evil.zip", {"link.gba": b"/etc/passwd", "ok.gba": gba_rom()}, attrs={"link.gba": 0o120777})
        u = self.attack(self.imp1 / "evil.zip")
        self.assertIn("symlink", " ".join(u["reasons"]))

    def test_device_in_zip(self):
        mkzip(self.imp1 / "evil.zip", {"dev.gba": b""}, attrs={"dev.gba": 0o020666})
        self.attack(self.imp1 / "evil.zip")

    def test_zip_bomb_ratio(self):
        mkzip(self.imp1 / "bomb.zip", {"big.gba": b"\x00" * (40 * MB)})
        self.assertLess((self.imp1 / "bomb.zip").stat().st_size, 200 * 1024)
        u = self.attack(self.imp1 / "bomb.zip")
        self.assertIn("zip bomb", " ".join(u["reasons"]))

    def test_per_file_size_limit(self):
        mkzip(self.imp1 / "big.zip", {"a.gba": gba_rom(size=0x400)})
        u = self.attack(self.imp1 / "big.zip", IM.Limits(max_file_bytes=100))
        self.assertIn("quá lớn", " ".join(u["reasons"]))

    def test_stage_counts_real_bytes_not_declared(self):
        # kích thước khai báo nhỏ hơn dữ liệu thật: bộ đếm byte thật của staging phải chặn
        z = self.imp1 / "lie.zip"
        mkzip(z, {"a.gba": gba_rom(size=0x400)}, method=zipfile.ZIP_STORED)
        src = IM.collect_zip(z, "primary", True, IM.DEFAULT_LIMITS)
        m = src.members[0]
        m.size = 10                                         # header nói 10 byte
        with self.assertRaises(IM.ImportBlocked):
            IM._stage_member(m, self.tmp / "st" / "a.gba", self.tmp / "st", IM.DEFAULT_LIMITS, [0])
        src.close()

    def test_too_many_entries(self):
        mkzip(self.imp1 / "many.zip", {f"f{i}.gba": gba_rom(seed=i) for i in range(12)})
        u = self.attack(self.imp1 / "many.zip", IM.Limits(max_entries=10))
        self.assertIn("quá nhiều entry", " ".join(u["reasons"]))

    def test_too_deep(self):
        mkzip(self.imp1 / "deep.zip", {"a/b/c/d/e/f/g/h/i/j/x.gba": gba_rom()})
        self.attack(self.imp1 / "deep.zip", IM.Limits(max_depth=3))

    def test_total_size_limit(self):
        mkzip(self.imp1 / "t.zip", {"a.gba": gba_rom(size=0x400), "b.gba": gba_rom(size=0x800, seed=2)})
        self.attack(self.imp1 / "t.zip", IM.Limits(max_total_bytes=1000))

    def test_executable_by_extension_and_magic(self):
        for name, data in (("run.sh", b"#!/bin/sh\necho hi\n"), ("run.exe", b"MZ" + b"\x00" * 100),
                           ("tool.py", b"print(1)"), ("lib.so", b"x"), ("a.apk", b"PK")):
            with self.subTest(name=name):
                z = self.imp1 / "x.zip"
                mkzip(z, {name: data, "ok.gba": gba_rom()})
                self.attack(z)
                z.unlink()
        mkzip(self.imp1 / "x.zip", {"game.gba": b"\x7fELF" + b"\x00" * 100})   # đuôi hiền nhưng nội dung ELF
        u = self.attack(self.imp1 / "x.zip")
        self.assertIn("ELF", " ".join(u["reasons"]))

    def test_pe_executable_disguised(self):
        pe = bytearray(0x100)
        pe[0:2] = b"MZ"
        struct.pack_into("<I", pe, 0x3C, 0x80)
        pe[0x80:0x84] = b"PE\x00\x00"
        mkzip(self.imp1 / "x.zip", {"game.bin": bytes(pe)})
        u = self.attack(self.imp1 / "x.zip")
        self.assertIn("PE", " ".join(u["reasons"]))

    def test_nested_archive(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("a.gba", gba_rom())
        mkzip(self.imp1 / "outer.zip", {"inner.zip": inner.getvalue()})
        u = self.attack(self.imp1 / "outer.zip")
        self.assertIn("lồng", " ".join(u["reasons"]))

    def test_bad_crc_blocked(self):
        z = self.imp1 / "crc.zip"
        data = gba_rom(size=0x400)
        mkzip(z, {"a.gba": data}, method=zipfile.ZIP_STORED)
        raw = bytearray(z.read_bytes())
        i = raw.index(data[0x200:0x210] + b"") if data[0x200:0x210] != b"\x00" * 16 else raw.index(data[0x100:0x110])
        raw[i + 5] ^= 0xFF
        z.write_bytes(bytes(raw))
        self.attack(z)

    def test_bad_crc_in_large_member_caught_when_extracting(self):
        z = self.imp1 / "crc2.zip"
        data = gba_rom(size=0x40000)
        data = data[:0x20000] + os.urandom(0x20000)
        mkzip(z, {"a.gba": data}, method=zipfile.ZIP_STORED)
        raw = bytearray(z.read_bytes())
        raw[-0x1000] ^= 0xFF           # hỏng ở đuôi: phần header đọc (64 KiB đầu) không chạm tới
        z.write_bytes(bytes(raw))
        before = tree(self.tmp)
        plan = self.plan()
        self.assertEqual(self.unit(plan)["status"], "ready")      # scan chỉ đọc đầu file
        res = IM.apply_plan(plan, self.cards, self.ud)
        self.assertEqual(res["installed"], 0, res)
        self.assertEqual(res["blocked"], 1)
        self.assertIn("CRC", res["results"][0]["message"])
        self.assertEqual(list((self.sd2 / "Roms").rglob("*.gba")), [])
        self.assertEqual(self.outside(before), [])

    def test_encrypted_entry(self):
        z = self.imp1 / "enc.zip"
        mkzip(z, {"a.gba": gba_rom()})
        raw = bytearray(z.read_bytes())
        # đặt cờ mật khẩu (bit 0) ở cả local header và central directory
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):
            i = raw.index(sig)
            off = i + (6 if sig == b"PK\x03\x04" else 8)
            raw[off] |= 1
        z.write_bytes(bytes(raw))
        self.attack(z)

    def test_duplicate_names_case_insensitive(self):
        z = self.imp1 / "dup.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("A.gba", gba_rom())
            zf.writestr("a.GBA", gba_rom(seed=1))
        self.attack(z)

    def test_not_a_zip(self):
        self.w(self.imp1 / "fake.zip", b"this is not a zip")
        u = self.attack(self.imp1 / "fake.zip")
        self.assertIn("ZIP", " ".join(u["reasons"]))

    def test_directory_source_symlink_blocked(self):
        d = self.imp1 / "pack"
        self.w(d / "a.gba", gba_rom())
        try:
            os.symlink(self.tmp, d / "evil")
        except (OSError, NotImplementedError):
            self.skipTest("không tạo được symlink trên máy này")
        u = self.unit(self.scan())
        self.assertEqual(u["status"], "blocked")
        self.assertIn("symlink", " ".join(u["reasons"]))

    def test_fat_forbidden_chars_single_rom_sanitized_multi_blocked(self):
        mkzip(self.imp1 / "a.zip", {"Zelda: Link?.gb": gb_rom()})
        u = self.unit(self.scan())
        self.assertEqual(u["files"][0]["dest_rel"], "Zelda_ Link_.gb")
        self.assertTrue(any("FAT32" in w for w in u["warnings"]))
        (self.imp1 / "a.zip").unlink()
        mkzip(self.imp1 / "b.zip", {"G: 1.cue": b'FILE "G: 1.bin" BINARY\r\n', "G: 1.bin": psx_bin()})
        self.assertEqual(self.unit(self.scan())["status"], "blocked")


# ======================================================================================
class TestCardChoice(Base):
    def setUp(self):
        super().setUp()
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})

    def test_default_is_secondary_with_reason(self):
        p = self.plan()
        self.assertTrue(p["ok"])
        self.assertEqual(p["card"]["selected"], "secondary")
        self.assertIn("thẻ phụ", p["card"]["reason"])
        res = self.apply(p)
        self.assertEqual(res["installed"], 1)
        self.assertTrue((self.sd2 / "Roms" / "GBA" / "g.gba").is_file())
        self.assertFalse((self.sd1 / "Roms" / "GBA").exists())

    def test_secondary_too_small_asks_never_silent_fallback(self):
        self.free[str(self.sd2)] = 50 * MB
        p = self.plan()
        self.assertFalse(p["ok"])
        self.assertTrue(p["needs_card_choice"])
        self.assertIsNone(p["card"]["selected"])
        self.assertIn("không đủ chỗ", p["card"]["reason"])
        before = tree(self.tmp)
        res = IM.apply_plan(p, self.cards, self.ud)
        self.assertEqual(res["installed"], 0)
        self.assertFalse(res["success"])
        self.assertEqual(tree(self.tmp), before)       # không ghi gì lên thẻ nào
        self.assertFalse((self.sd1 / "Roms" / "GBA").exists())

    def test_secondary_missing_asks(self):
        cards = {"primary": self.sd1, "secondary": None}
        p = IM.build_plan(None, cards, self.ud)
        self.assertTrue(p["needs_card_choice"])
        self.assertEqual(p["card"]["selected"], None)

    def test_secondary_readonly_asks(self):
        with mock.patch.object(IM.os, "access", lambda path, mode: str(path) != str(self.sd2)):
            p = self.plan()
        self.assertTrue(p["needs_card_choice"])
        self.assertIn("chỉ đọc", p["card"]["reason"])

    def test_explicit_primary_after_asking(self):
        self.free[str(self.sd2)] = 50 * MB
        p = self.plan(pref="primary")
        self.assertTrue(p["ok"])
        self.assertEqual(p["card"]["selected"], "primary")
        res = self.apply(p)
        self.assertEqual(res["installed"], 1, res)
        self.assertTrue((self.sd1 / "Roms" / "GBA" / "g.gba").is_file())

    def test_explicit_primary_not_enough_space(self):
        self.free[str(self.sd1)] = 10 * MB
        p = self.plan(pref="primary")
        self.assertFalse(p["ok"])
        self.assertIsNone(p["card"]["selected"])

    def test_space_recomputed_at_apply(self):
        p = self.plan()
        self.free[str(self.sd2)] = 10 * MB            # thẻ đầy giữa lúc xem trước và lúc cài
        before = tree(self.tmp)
        res = IM.apply_plan(p, self.cards, self.ud)
        self.assertEqual(res["installed"], 0)
        self.assertIn("không đủ chỗ", res["message"])
        self.assertEqual(tree(self.tmp), before)


# ======================================================================================
class TestInstallRules(Base):
    def test_same_hash_already_present_not_installed(self):
        rom = gba_rom(seed=5)
        self.w(self.sd1 / "Roms" / "GBA" / "Old Name.gba", rom)          # đã có ở thẻ kia, tên khác
        mkzip(self.imp1 / "g.zip", {"New Name.gba": rom})
        p = self.plan()
        self.assertEqual(self.unit(p)["action"], "present")
        res = self.apply(p)
        self.assertEqual((res["installed"], res["already"]), (0, 1))
        self.assertFalse((self.sd2 / "Roms" / "GBA" / "New Name.gba").exists())

    def test_same_name_different_hash_keep_is_default(self):
        old = self.w(self.sd2 / "Roms" / "GBA" / "g.gba", gba_rom(seed=1))
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(seed=2)})
        p = self.plan()
        u = self.unit(p)
        self.assertEqual(u["action"], "conflict")
        self.assertEqual(u["conflict"]["options"], ["keep", "replace", "copy", "cancel"])
        res = self.apply(p)
        self.assertEqual(res["results"][0]["status"], "kept")
        self.assertEqual(old.read_bytes(), gba_rom(seed=1))
        self.assertTrue((self.imp1 / "g.zip").exists())            # chưa cài -> file gốc ở nguyên chỗ

    def test_conflict_replace_moves_old_to_trash(self):
        old = self.w(self.sd2 / "Roms" / "GBA" / "g.gba", gba_rom(seed=1))
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(seed=2)})
        p = self.plan()
        uid = self.unit(p)["id"]
        res = self.apply(p, conflicts={uid: "replace"})
        self.assertEqual(res["installed"], 1, res)
        self.assertEqual(old.read_bytes(), gba_rom(seed=2))
        trash = list((self.sd2 / ".loongmate-import" / "trash").rglob("g.gba"))
        self.assertEqual(len(trash), 1)
        self.assertEqual(trash[0].read_bytes(), gba_rom(seed=1))

    def test_conflict_copy_installs_numbered_copy(self):
        old = self.w(self.sd2 / "Roms" / "GBA" / "g.gba", gba_rom(seed=1))
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(seed=2)})
        p = self.plan()
        res = self.apply(p, conflicts={self.unit(p)["id"]: "copy"})
        self.assertEqual(res["installed"], 1, res)
        self.assertEqual(old.read_bytes(), gba_rom(seed=1))
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "g (2).gba").read_bytes(), gba_rom(seed=2))

    def test_conflict_copy_multifile_goes_to_subfolder_keeping_refs(self):
        cue = b'FILE "G.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n'
        self.w(self.sd2 / "Roms" / "PSX" / "G.cue", b"OLD CUE")
        mkzip(self.imp1 / "g.zip", {"G.cue": cue, "G.bin": psx_bin()})
        p = self.plan()
        res = self.apply(p, conflicts={self.unit(p)["id"]: "copy"})
        self.assertEqual(res["installed"], 1, res)
        d = self.sd2 / "Roms" / "PSX" / "G (2)"
        self.assertEqual(sorted(x.name for x in d.iterdir()), ["G.bin", "G.cue"])
        self.assertEqual((self.sd2 / "Roms" / "PSX" / "G.cue").read_bytes(), b"OLD CUE")

    def test_multifile_partly_identical_replaces_only_the_different_file(self):
        bin_data = psx_bin()
        self.w(self.sd2 / "Roms" / "PSX" / "G.bin", bin_data)                 # track giống hệt đã nằm đúng chỗ
        self.w(self.sd2 / "Roms" / "PSX" / "G.cue", b"OLD CUE")               # cue khác nội dung
        mkzip(self.imp1 / "g.zip", {"G.cue": b'FILE "G.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n', "G.bin": bin_data})
        p = self.plan()
        u = self.unit(p)
        self.assertEqual(u["action"], "conflict")
        self.assertEqual(len(u["conflict"]["paths"]), 1)
        res = self.apply(p, conflicts={u["id"]: "replace"})
        self.assertEqual(res["installed"], 1, res)
        self.assertTrue((self.sd2 / "Roms" / "PSX" / "G.cue").read_bytes().startswith(b"FILE"))
        self.assertEqual((self.sd2 / "Roms" / "PSX" / "G.bin").read_bytes(), bin_data)
        self.assertEqual([x.name for x in (self.sd2 / ".loongmate-import" / "trash").rglob("*") if x.is_file()], ["G.cue"])

    def test_content_present_elsewhere_beats_name_taken_by_other_content(self):
        rom = gba_rom(seed=5)
        self.w(self.sd1 / "Roms" / "GBA" / "Elsewhere.gba", rom)               # nội dung đã có ở thẻ kia
        self.w(self.sd2 / "Roms" / "GBA" / "g.gba", gba_rom(seed=6))           # nhưng tên g.gba đang bị chiếm bởi thứ khác
        mkzip(self.imp1 / "g.zip", {"g.gba": rom})
        p = self.plan()
        self.assertEqual(self.unit(p)["action"], "present")
        res = self.apply(p)
        self.assertEqual((res["installed"], res["already"]), (0, 1))
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "g.gba").read_bytes(), gba_rom(seed=6))

    def test_conflict_cancel(self):
        self.w(self.sd2 / "Roms" / "GBA" / "g.gba", gba_rom(seed=1))
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(seed=2)})
        p = self.plan()
        res = self.apply(p, conflicts={self.unit(p)["id"]: "cancel"})
        self.assertEqual(res["results"][0]["status"], "cancelled")

    def test_never_overwrites_saves_states_bios(self):
        sv = self.w(self.sd2 / "Saves" / "g.srm", b"MY SAVE")
        st = self.w(self.sd2 / "States" / "g.state", b"MY STATE")
        bi = self.w(self.sd2 / "BIOS" / "g.gba", b"MY BIOS")
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(), "g.srm": b"ZIP SAVE", "g.state": b"ZIP STATE"})
        res = self.apply()
        self.assertEqual(res["installed"], 1, res)
        self.assertEqual((sv.read_bytes(), st.read_bytes(), bi.read_bytes()), (b"MY SAVE", b"MY STATE", b"MY BIOS"))
        self.assertEqual(sorted(p.name for p in (self.sd2 / "Roms" / "GBA").iterdir()), ["g.gba"])

    def test_tampered_plan_cannot_redirect_destination(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        p = self.plan()
        p["units"][0]["files"][0]["dest_rel"] = "../../Saves/g.srm"        # kế hoạch bị sửa
        p["units"][0]["folder"] = "../../Saves"
        res = IM.apply_plan(p, self.cards, self.ud)
        self.assertFalse((self.sd2 / "Saves" / "g.srm").exists())
        self.assertEqual(list((self.sd2 / "Saves").iterdir()), [])
        self.assertEqual(res["installed"], 1)                              # cài theo nội dung thật, đúng chỗ
        self.assertTrue((self.sd2 / "Roms" / "GBA" / "g.gba").is_file())

    def test_needs_choice_requires_choice_in_candidates(self):
        self.w(self.imp1 / "x.bin", b"\x00" * 3000)
        p = self.plan()
        u = self.unit(p)
        self.assertEqual(u["action"], "choose_system")
        r1 = self.apply(p)
        self.assertEqual(r1["skipped"], 1)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        r2 = self.apply(p, choices={u["id"]: "NES"})
        self.assertEqual(r2["blocked"], 1)
        r3 = self.apply(p, choices={u["id"]: "ATARI2600"})
        self.assertEqual(r3["installed"], 1, r3)
        self.assertTrue((self.sd2 / "Roms" / "ATARI2600" / "x.bin").is_file())

    def test_folder_created_from_systems_json(self):
        self.w(self.imp1 / "a.gen", genesis_rom())
        self.apply()
        self.assertTrue((self.sd2 / "Roms" / "GENESIS" / "a.gen").is_file())

    def test_select_limits_plan(self):
        self.w(self.imp1 / "a.gba", gba_rom())
        self.w(self.imp1 / "b.nes", nes_rom())
        r = self.scan()
        only = self.unit(r, "b")["id"]
        p = self.plan(select=[only])
        self.assertEqual([u["name"] for u in p["units"]], ["b"])
        res = self.apply(p)
        self.assertEqual(res["installed"], 1)
        self.assertTrue((self.imp1 / "a.gba").exists())
        self.assertFalse((self.sd2 / "Roms" / "GBA").exists())

    def test_source_moved_to_done_only_after_success(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        self.w(self.imp2 / "n.nes", nes_rom())
        res = self.apply()
        self.assertEqual(res["installed"], 2, res)
        self.assertFalse((self.imp1 / "g.zip").exists())
        self.assertTrue((self.imp1 / ".done" / "g.zip").is_file())
        self.assertTrue((self.imp2 / ".done" / "n.nes").is_file())

    def test_delete_source_when_enabled(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        res = self.apply(delete_source=True)
        self.assertEqual(res["installed"], 1)
        self.assertFalse((self.imp1 / "g.zip").exists())
        self.assertFalse((self.imp1 / ".done").exists())

    def test_custom_path_source_never_moved_or_deleted(self):
        z = mkzip(self.tmp / "mine.zip", {"g.gba": gba_rom()})
        p = self.plan([z])
        res = IM.apply_plan(p, self.cards, self.ud, delete_source=True)
        self.assertEqual(res["installed"], 1)
        self.assertTrue(z.exists())

    def test_partial_zip_source_kept(self):
        mkzip(self.imp1 / "p.zip", {"a.gba": gba_rom(), "b.nes": nes_rom()})
        r = self.scan()
        p = self.plan(select=[self.unit(r, "a")["id"]])
        res = IM.apply_plan(p, self.cards, self.ud)
        self.assertEqual(res["installed"], 1)
        self.assertTrue((self.imp1 / "p.zip").exists())

    def test_source_changed_after_plan_is_refused(self):
        z = mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        p = self.plan()
        mkzip(z, {"g.gba": gba_rom(seed=9), "extra.txt": b"x"})
        res = IM.apply_plan(p, self.cards, self.ud)
        self.assertEqual((res["installed"], res["blocked"]), (0, 1))
        self.assertEqual(list((self.sd2 / "Roms").rglob("*.gba")), [])

    def test_ledger_and_installed_manifest(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        self.apply()
        events = [json.loads(x)["event"] for x in (self.ud / "import-ledger.jsonl").read_text().splitlines()]
        self.assertEqual(events[0], "job_start")
        self.assertIn("unit_committed", events)
        self.assertEqual(events[-1], "job_done")
        m = [json.loads(x) for x in (self.sd2 / ".loongmate-import" / "installed.jsonl").read_text().splitlines()]
        self.assertEqual(m[0]["files"][0]["sha256"], sha(gba_rom()))
        self.assertEqual(m[0]["files"][0]["path"].replace("\\", "/"), "Roms/GBA/g.gba")

    def test_no_leftover_staging_after_success(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        self.apply()
        left = list((self.sd2 / ".loongmate-import" / "staging").iterdir())
        self.assertEqual(left, [])
        self.assertEqual([p.name for p in (self.sd2 / "Roms" / "GBA").iterdir()], ["g.gba"])

    def test_staging_is_on_target_card(self):
        seen = []

        def spy(name):
            if name == "after_stage":
                seen.append([str(p) for p in (self.sd2 / ".loongmate-import" / "staging").rglob("*") if p.is_file()])
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        with mock.patch.object(IM, "_checkpoint", spy):
            self.apply()
        self.assertTrue(any("g.gba" in x for x in seen[0]))

    def test_rom_not_visible_in_roms_before_commit(self):
        seen = []

        def spy(name):
            if name == "after_stage":
                seen.append(list((self.sd2 / "Roms").rglob("*")))
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        with mock.patch.object(IM, "_checkpoint", spy):
            self.apply()
        self.assertEqual(seen, [[]])


# ======================================================================================
class _Crash(BaseException):
    """Mô phỏng mất điện: không bị `except Exception` của importer bắt, nên không có đoạn dọn dẹp nào chạy."""


class TestCrashRecovery(Base):
    def crash_at(self, point):
        def hook(name):
            if name == point:
                raise _Crash(point)
        return mock.patch.object(IM, "_checkpoint", hook)

    def setUp(self):
        super().setUp()
        cue = b'FILE "G.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n'
        mkzip(self.imp1 / "g.zip", {"G.cue": cue, "G.bin": psx_bin(), "x.nes": nes_rom()})
        self.plan0 = self.plan()

    def staging_jobs(self):
        return [p for p in (self.sd2 / ".loongmate-import" / "staging").glob("*") if p.is_dir()]

    def test_crash_after_staging_leaves_roms_clean_and_recover_cleans(self):
        with self.crash_at("after_stage"):
            with self.assertRaises(_Crash):
                IM.apply_plan(self.plan0, self.cards, self.ud)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.assertEqual(len(self.staging_jobs()), 1)
        jobs = IM.list_jobs(self.cards, self.ud)
        self.assertEqual(len(jobs["pending"]), 1)
        self.assertEqual(jobs["pending"][0]["state"], "staged")
        rep = IM.recover(self.cards, self.ud)
        self.assertEqual(rep["recovered"][0]["action"], "cleaned")
        self.assertEqual(self.staging_jobs(), [])
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.assertTrue((self.imp1 / "g.zip").exists())                  # nguồn còn nguyên
        self.assertEqual(IM.list_jobs(self.cards, self.ud)["pending"], [])

    def test_crash_mid_commit_rolls_back_half_placed_set(self):
        with self.crash_at("mid_commit"):
            with self.assertRaises(_Crash):
                IM.apply_plan(self.plan0, self.cards, self.ud)
        names = [p.name for p in (self.sd2 / "Roms").rglob("*") if p.is_file()]
        self.assertTrue(names, "mô phỏng phải để lại file nửa vời trước khi recover")
        rep = IM.recover(self.cards, self.ud)
        self.assertTrue(rep["recovered"][0]["notes"])
        self.assertEqual([p for p in (self.sd2 / "Roms").rglob("*") if p.is_file()], [])
        self.assertEqual(self.staging_jobs(), [])

    def test_hidden_temp_names_during_commit(self):
        seen = []

        def spy(name):
            if name == "mid_commit":
                seen.append([p.name for p in (self.sd2 / "Roms").rglob("*") if p.is_file()])
        with mock.patch.object(IM, "_checkpoint", spy):
            IM.apply_plan(self.plan0, self.cards, self.ud)
        self.assertTrue(seen[0] and all(n.startswith(".") for n in seen[0]))      # tên tạm ẩn (inventory bỏ qua)

    def test_recover_keeps_already_committed_units(self):
        calls = {"n": 0}

        def hook(name):
            if name == "before_commit":
                calls["n"] += 1
                if calls["n"] == 2:
                    raise _Crash(name)
        with mock.patch.object(IM, "_checkpoint", hook):
            with self.assertRaises(_Crash):
                IM.apply_plan(self.plan0, self.cards, self.ud)
        placed = sorted(p.relative_to(self.sd2 / "Roms").as_posix() for p in (self.sd2 / "Roms").rglob("*") if p.is_file())
        self.assertEqual(placed, ["PSX/G.bin", "PSX/G.cue"])     # đúng unit thứ nhất (2 file) đã commit xong
        rep = IM.recover(self.cards, self.ud)
        self.assertEqual(rep["recovered"][0]["kept_units"], 1)
        after = sorted(p.relative_to(self.sd2 / "Roms").as_posix() for p in (self.sd2 / "Roms").rglob("*") if p.is_file())
        self.assertEqual(after, placed)

    def test_recover_keeps_staging_when_job_json_unreadable(self):
        # job.json hỏng / của bản LoongMate khác: không biết unit nào đang commit -> KHÔNG xoá mù, giữ để xử lý sau
        d = self.sd2 / ".loongmate-import" / "staging" / "20261017-120000-abcdef"
        (d / "u1").mkdir(parents=True)
        (d / "u1" / "Game.gba").write_bytes(b"x" * 10)
        (d / "job.json").write_text('{"schema": 99, "units": []}', encoding="utf-8")
        rep = IM.recover(self.cards, self.ud)
        self.assertEqual([(r["job_id"], r["action"]) for r in rep["recovered"]], [(d.name, "kept")])
        self.assertTrue((d / "u1" / "Game.gba").is_file())

    def test_recover_replace_restores_old_file(self):
        old = self.w(self.sd2 / "Roms" / "NES" / "x.nes", nes_rom(seed=7))
        nes = [u for u in self.plan()["units"] if u["name"] == "x"][0]
        plan = self.plan(select=[nes["id"]])
        self.assertEqual(plan["units"][0]["action"], "conflict")
        with self.crash_at("mid_commit"):
            with self.assertRaises(_Crash):
                IM.apply_plan(plan, self.cards, self.ud, conflicts={nes["id"]: "replace"}, choices={})
        IM.recover(self.cards, self.ud)
        self.assertEqual(old.read_bytes(), nes_rom(seed=7))

    def test_real_process_kill_after_staging(self):
        # tiến trình thật bị giết (os._exit) ngay sau staging
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(self.plan0))
        script = textwrap.dedent(f"""
            import json, os, sys
            sys.path.insert(0, {str(ROOT)!r})
            import engine.importer as IM
            from pathlib import Path
            IM._free = lambda root: 10 * 1024**4
            IM._checkpoint = lambda name: os._exit(137) if name == "after_stage" else None
            plan = json.load(open({str(plan_path)!r}))
            cards = {{"primary": Path({str(self.sd1)!r}), "secondary": Path({str(self.sd2)!r})}}
            IM.apply_plan(plan, cards, Path({str(self.ud)!r}))
        """)
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 137, r.stderr)
        self.assertEqual(len(self.staging_jobs()), 1)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        rep = IM.recover(self.cards, self.ud)
        self.assertEqual(rep["recovered"][0]["action"], "cleaned")
        self.assertEqual(self.staging_jobs(), [])
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])

    def test_apply_cleans_dead_job_first(self):
        with self.crash_at("after_stage"):
            with self.assertRaises(_Crash):
                IM.apply_plan(self.plan0, self.cards, self.ud)
        res = IM.apply_plan(self.plan(), self.cards, self.ud)
        self.assertEqual(res["installed"], 2, res)
        self.assertEqual(self.staging_jobs(), [])

    def test_failure_in_one_unit_does_not_stop_others_and_leaves_no_partial(self):
        real = IM._commit
        n = {"i": 0}

        def flaky(rec, job, job_dir, root, pairs, trash, app_data, u, r, catalog):
            n["i"] += 1
            if n["i"] == 1:
                # đặt một file rồi hỏng giữa chừng
                os_rename = IM.os.rename
                calls = {"c": 0}

                def bad(a, b):
                    calls["c"] += 1
                    if calls["c"] == 2:
                        raise OSError("giả lập lỗi ghi")
                    return os_rename(a, b)
                with mock.patch.object(IM.os, "rename", bad):
                    return real(rec, job, job_dir, root, pairs, trash, app_data, u, r, catalog)
            return real(rec, job, job_dir, root, pairs, trash, app_data, u, r, catalog)
        with mock.patch.object(IM, "_commit", flaky):
            res = IM.apply_plan(self.plan0, self.cards, self.ud)
        statuses = sorted(r["status"] for r in res["results"])
        self.assertEqual(statuses, ["failed", "installed"])
        files = [p.name for p in (self.sd2 / "Roms").rglob("*") if p.is_file()]
        self.assertEqual(len(files), 1)           # unit lỗi đã được hoàn tác; unit kia nguyên vẹn


# ======================================================================================
class TestPlanReadOnly(Base):
    def test_plan_and_scan_write_nothing_even_with_everything(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom(), "h.nes": nes_rom()})
        self.w(self.imp2 / "x.bin", b"\x00" * 100)
        before = tree(self.tmp)
        self.scan()
        self.plan()
        self.assertEqual(tree(self.tmp), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["sd1", "sd2", "ud"])
        self.assertFalse((self.sd2 / ".loongmate-import").exists())


class TestCli(Base):
    def setUp(self):
        super().setUp()
        import engine.cli as CLI
        import engine.jawaka as jawaka
        self.CLI = CLI
        self.rescan = mock.Mock(return_value={"ok": True, "message": "Quét xong.", "waited_s": 0.1})
        for pp in (mock.patch.object(jawaka, "rescan_available", lambda *a, **k: {"available": True, "reason": ""}),
                   mock.patch.object(jawaka, "request_rescan", self.rescan)):
            pp.start()
            self.addCleanup(pp.stop)

    def run_cli(self, *argv, stdin=""):
        import contextlib
        base = ["--quiet", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            code = self.CLI.main(base + list(argv))
        return code, json.loads(out.getvalue())

    def test_scan_is_read_only(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        before = tree(self.tmp)
        code, out = self.run_cli("import", "scan")
        self.assertEqual(code, 0)
        self.assertEqual(out["units"][0]["system"], "GBA")
        self.assertEqual(tree(self.tmp), before)
        self.rescan.assert_not_called()

    def test_plan_without_plan_out_writes_nothing(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        before = tree(self.tmp)
        code, out = self.run_cli("import", "plan")
        self.assertEqual((code, out["ok"], out["card"]["selected"]), (0, True, "secondary"))
        self.assertEqual(tree(self.tmp), before)

    def test_plan_then_apply_end_to_end(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        pf = self.tmp / "plan.json"
        code, out = self.run_cli("import", "plan", "--plan-out", str(pf))
        self.assertEqual(code, 0)
        self.assertTrue(pf.exists())
        code, res = self.run_cli("import", "apply", "--plan", str(pf), "--yes")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["installed"], 1)
        self.assertEqual(res["rescan"]["ok"], True)
        self.rescan.assert_called_once()
        self.assertTrue((self.sd2 / "Roms" / "GBA" / "g.gba").is_file())
        self.assertTrue((self.imp1 / ".done" / "g.zip").is_file())

    def test_apply_refuses_without_yes_when_not_tty(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        pf = self.tmp / "plan.json"
        self.run_cli("import", "plan", "--plan-out", str(pf))
        before = tree(self.tmp)
        code, res = self.run_cli("import", "apply", "--plan", str(pf))
        self.assertEqual(code, 1)
        self.assertFalse(res["success"])
        self.assertEqual(tree(self.tmp), before)

    def test_apply_rejects_tampered_plan(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        pf = self.tmp / "plan.json"
        self.run_cli("import", "plan", "--plan-out", str(pf))
        doc = json.loads(pf.read_text())
        doc["items"][0]["system"] = "NES"
        pf.write_text(json.dumps(doc))
        code, res = self.run_cli("import", "apply", "--plan", str(pf), "--yes")
        self.assertEqual(code, 3)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])

    def test_choice_and_conflict_flags(self):
        self.w(self.imp1 / "x.bin", b"\x00" * 3000)
        self.w(self.sd2 / "Roms" / "ATARI2600" / "x.bin", b"\x01" * 3000)
        pf = self.tmp / "plan.json"
        _, out = self.run_cli("import", "plan", "--plan-out", str(pf))
        uid = out["units"][0]["id"]
        code, res = self.run_cli("import", "apply", "--plan", str(pf), "--yes", "--choice", f"{uid}=ATARI2600",
                                 "--conflict", f"{uid}=copy")
        self.assertEqual(code, 0, res)
        self.assertEqual((self.sd2 / "Roms" / "ATARI2600" / "x (2).bin").read_bytes(), b"\x00" * 3000)

    def test_secondary_full_plan_needs_card_and_apply_does_nothing(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        self.free[str(self.sd2)] = 1 * MB
        pf = self.tmp / "plan.json"
        code, out = self.run_cli("import", "plan", "--plan-out", str(pf))
        self.assertFalse(out["ok"])
        self.assertTrue(out["needs_card_choice"])
        before = tree(self.tmp)
        code, res = self.run_cli("import", "apply", "--plan", str(pf), "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(tree(self.tmp), before)
        code, out = self.run_cli("import", "plan", "--card", "primary", "--plan-out", str(pf))
        self.assertTrue(out["ok"])
        code, res = self.run_cli("import", "apply", "--plan", str(pf), "--yes")
        self.assertEqual(res["card"]["selected"], "primary")
        self.assertTrue((self.sd1 / "Roms" / "GBA" / "g.gba").is_file())

    def test_jobs_and_recover(self):
        mkzip(self.imp1 / "g.zip", {"g.gba": gba_rom()})
        with mock.patch.object(IM, "_checkpoint", lambda n: (_ for _ in ()).throw(_Crash(n)) if n == "after_stage" else None):
            with self.assertRaises(_Crash):
                IM.apply_plan(self.plan(), self.cards, self.ud)
        code, out = self.run_cli("import", "jobs")
        self.assertEqual(len(out["pending"]), 1)
        code, res = self.run_cli("import", "recover")                    # không --yes, không TTY
        self.assertEqual(code, 1)
        self.assertEqual(len(self.run_cli("import", "jobs")[1]["pending"]), 1)
        code, res = self.run_cli("import", "recover", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.run_cli("import", "jobs")[1]["pending"], [])

    def test_mkdir(self):
        shutil.rmtree(self.imp1)
        shutil.rmtree(self.imp2)
        code, out = self.run_cli("import", "mkdir", "--card", "secondary")
        self.assertEqual(code, 0)
        self.assertTrue(self.imp2.is_dir())
        self.assertFalse(self.imp1.exists())

    def test_7z_reported_unsupported_via_cli(self):
        self.w(self.imp1 / "a.7z", b"7z" + b"0" * 20)
        _, out = self.run_cli("import", "scan")
        self.assertEqual(out["units"][0]["status"], "unsupported")


class TestUiFlow(Base):
    """Logic màn Nhập game của giao diện Python (không cần SDL): bấm phím như người dùng."""

    def setUp(self):
        super().setUp()
        from engine import ui_screens as ui
        import engine.importer as real
        from engine.inventory import InventoryEngine
        from engine.probe import PlatformProbe
        self.ui, self.real = ui, real
        for pp in (mock.patch.dict(os.environ, {ui.NO_RESCAN_ENV: "1"}),
                   mock.patch.object(real, "_free", lambda root: self.free.get(str(root), 10 * 1024 * MB))):
            pp.start()
            self.addCleanup(pp.stop)
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        self.app = ui.App(probe, None, None, InventoryEngine(probe.primary_path, probe.secondary_path), None, self.ud, None)

    def goto(self, scr, text):
        idx = [i for i, it in enumerate(scr.items) if text in it.label]
        self.assertTrue(idx, [it.label for it in scr.items])
        while scr.sel != idx[0]:
            self.app.on_action("DOWN" if scr.sel < idx[0] else "UP")

    def open(self):
        self.app.open_import()
        self.app.wait_task()
        return self.app.cur()

    def test_select_plan_confirm_install_stays_put(self):
        self.w(self.imp1 / "a.gba", gba_rom())
        self.w(self.imp2 / "b.nes", nes_rom())
        scr = self.open()
        depth = len(self.app.stack)
        self.assertEqual(scr.title, "NHẬP GAME")
        self.goto(scr, "[ ] a")
        self.app.on_action("X")
        self.goto(scr, "[ ] b")
        self.app.on_action("X")
        self.goto(scr, "Nhập 2 mục")
        self.app.on_action("A")
        self.app.wait_task()
        plan_scr = self.app.cur()
        self.assertEqual(plan_scr.title, "KẾ HOẠCH NHẬP")
        self.goto(plan_scr, "THỰC HIỆN")
        self.app.on_action("A")
        dlg = self.app.dialog
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.choice, 0)                       # mặc định Huỷ
        self.app.on_action("A")                               # A trên Huỷ: đóng hộp, không làm gì
        self.assertIsNone(self.app.dialog)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.goto(plan_scr, "THỰC HIỆN")
        self.app.on_action("A")
        self.app.on_action("RIGHT")
        self.app.on_action("A")
        self.app.wait_task()
        self.assertEqual(len(self.app.stack), depth)          # đứng yên tại danh sách nhập
        self.assertIs(self.app.cur(), scr)
        self.assertTrue((self.sd2 / "Roms" / "GBA" / "a.gba").is_file())
        self.assertTrue((self.sd2 / "Roms" / "NES" / "b.nes").is_file())
        self.assertEqual([it for it in scr.items if getattr(it, "import_id", None)], [])    # đã chuyển vào .done
        self.assertIn("Kết quả nhập game", self.app.dialog.title)

    def test_ambiguous_asks_system_then_installs_to_chosen(self):
        self.w(self.imp1 / "x.bin", b"\x00" * 3000)
        scr = self.open()
        self.goto(scr, "[ ] x")
        self.assertEqual(scr.cur_item().tag, "chọn hệ")
        self.app.on_action("A")                               # chọn hệ
        pick = self.app.cur()
        self.assertEqual(pick.title, "CHỌN HỆ MÁY")
        self.goto(pick, "GENESIS")
        self.app.on_action("A")
        self.assertIs(self.app.cur(), scr)
        self.assertEqual(self.real_choice(), {"GENESIS"})
        self.goto(scr, "Nhập 1 mục")
        self.app.on_action("A")
        self.app.wait_task()
        self.goto(self.app.cur(), "THỰC HIỆN")
        self.app.on_action("A")
        self.app.on_action("RIGHT")
        self.app.on_action("A")
        self.app.wait_task()
        self.assertTrue((self.sd2 / "Roms" / "GENESIS" / "x.bin").is_file())

    def real_choice(self):
        return set(self.app.import_choices.values())

    def test_unresolved_choice_blocks_plan(self):
        self.w(self.imp1 / "x.bin", b"\x00" * 3000)
        scr = self.open()
        self.goto(scr, "[ ] x")
        self.app.on_action("X")
        self.goto(scr, "Nhập 1 mục")
        self.app.on_action("A")
        self.app.wait_task()
        self.assertEqual(self.app.dialog.title, "Chưa chọn hệ máy")
        self.assertEqual(self.app.cur(), scr)

    def test_secondary_full_asks_default_cancel_then_primary(self):
        self.w(self.imp1 / "a.gba", gba_rom())
        self.free[str(self.sd2)] = 5 * MB
        scr = self.open()
        self.goto(scr, "[ ] a")
        self.app.on_action("A")                               # nhập mục này
        self.app.wait_task()
        dlg = self.app.dialog
        self.assertEqual(dlg.title, "Thẻ phụ không dùng được")
        self.assertEqual(dlg.choice, 0)
        self.app.on_action("A")                               # Huỷ
        self.assertEqual(list((self.sd1 / "Roms").rglob("*")), [])
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.assertIs(self.app.cur(), scr)

    def test_conflict_cycle_and_blocked_item_has_no_action(self):
        self.w(self.sd2 / "Roms" / "GBA" / "a.gba", gba_rom(seed=1))
        self.w(self.imp1 / "a.gba", gba_rom(seed=2))
        mkzip(self.imp1 / "evil.zip", {"../x.gba": gba_rom()})
        scr = self.open()
        self.goto(scr, "evil")
        self.assertEqual(scr.cur_item().tag, "bị chặn")
        self.assertIsNone(scr.cur_item().x)
        self.goto(scr, "[ ] a")
        self.app.on_action("A")
        self.app.wait_task()
        ps = self.app.cur()
        self.goto(ps, "a")
        self.assertIn("trùng tên: keep", ps.cur_item().tag)
        self.app.on_action("A")
        self.assertIn("trùng tên: replace", ps.cur_item().tag)

    def test_mkdir_item_creates_dirs(self):
        shutil.rmtree(self.imp1)
        shutil.rmtree(self.imp2)
        scr = self.open()
        self.goto(scr, "Mở thư mục nhập")
        self.app.on_action("A")
        self.app.wait_task()
        self.assertTrue(self.imp1.is_dir() and self.imp2.is_dir())


class FakeRemote:
    """'Máy' giả là hai thư mục tạm; cùng giao diện với pc/import_push.SshRemote (ssh thật được thử trên máy thật)."""

    def __init__(self, sd1, sd2, ud, secondary=True, corrupt=False, free=None):
        self.sd1, self.sd2, self.ud = Path(sd1), Path(sd2), Path(ud)
        self.has_secondary, self.corrupt, self.free = secondary, corrupt, free
        self.calls = []

    def leaf(self, sdcard=None):
        from device_paths import LeafPaths
        return LeafPaths(str(self.sd1), str(self.ud))

    def secondary(self, leaf):
        return str(self.sd2) if self.has_secondary else None

    def free_bytes(self, d):
        return self.free if self.free is not None else 50 * 1024 * MB

    def mkdir(self, d):
        Path(d).mkdir(parents=True, exist_ok=True)

    def listdir(self, d):
        return sorted(x.name for x in Path(d).iterdir()) if Path(d).is_dir() else []

    def upload(self, files, dest_dir):
        self.calls.append(("upload", dest_dir))
        for local, arc in files:
            dst = Path(dest_dir) / arc
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = Path(local).read_bytes()
            if self.corrupt:
                data = data[:-1] + bytes([data[-1] ^ 1])
            dst.write_bytes(data)

    def sha256s(self, base, names, python):
        return {n: sha(Path(base, n).read_bytes()) for n in names if Path(base, n).exists()}

    def promote(self, incoming, dest, tops):
        for t in tops:
            os.rename(Path(incoming) / t, Path(dest) / t)
        Path(incoming).rmdir()

    def rmtree(self, d):
        if not Path(d).name.startswith(".incoming-"):
            raise RuntimeError("từ chối")
        shutil.rmtree(d, ignore_errors=True)

    def cli(self, engine_dir, python, leaf, secondary, args, timeout=900):
        import contextlib
        import engine.cli as CLI
        base = ["--quiet", "--primary", leaf.sdcard, "--secondary", secondary or "", "--userdata", str(self.ud), "--json", "import"]
        if not secondary:
            base = [x for x in base if x != "--secondary" and x != ""]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
            code = CLI.main(base + list(args))
        return json.loads(out.getvalue()), code


class TestPcPush(Base):
    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(ROOT / "pc"))
        import import_push
        self.P = import_push
        shutil.rmtree(self.imp1)
        shutil.rmtree(self.imp2)
        self.src = self.tmp / "pc"
        self.src.mkdir()
        self.rom = self.w(self.src / "Game.gba", gba_rom())
        pp = mock.patch("engine.importer._free", lambda root: self.free.get(str(root), 10 * 1024 * MB))
        pp.start()
        self.addCleanup(pp.stop)
        import engine.jawaka as jawaka
        for q in (mock.patch.object(jawaka, "rescan_available", lambda *a, **k: {"available": False, "reason": "test"}),):
            q.start()
            self.addCleanup(q.stop)

    def run_push(self, *argv, **kw):
        rem = FakeRemote(self.sd1, self.sd2, self.ud, **kw)
        code = self.P.main(list(argv), remote=rem)
        return code, rem

    def test_pushes_to_secondary_import_with_sha_check(self):
        code, rem = self.run_push(str(self.rom))
        self.assertEqual(code, 0)
        self.assertEqual((self.sd2 / "Import" / "Game.gba").read_bytes(), gba_rom())
        self.assertFalse((self.sd1 / "Import").exists())
        self.assertEqual([p.name for p in (self.sd2 / "Import").iterdir()], ["Game.gba"])    # không còn .incoming

    def test_no_secondary_is_error_never_silent_primary(self):
        code, rem = self.run_push(str(self.rom), secondary=False)
        self.assertEqual(code, 3)
        self.assertFalse((self.sd1 / "Import").exists())
        self.assertEqual(rem.calls, [])

    def test_explicit_primary(self):
        code, _ = self.run_push(str(self.rom), "--card", "primary", secondary=False)
        self.assertEqual(code, 0)
        self.assertTrue((self.sd1 / "Import" / "Game.gba").is_file())

    def test_corruption_in_transit_nothing_exposed(self):
        code, rem = self.run_push(str(self.rom), corrupt=True)
        self.assertEqual(code, 1)
        self.assertEqual(list((self.sd2 / "Import").iterdir()), [])         # không file mới, thư mục tạm đã xoá

    def test_name_clash_refused_whole_batch(self):
        self.w(self.sd2 / "Import" / "game.gba", b"old")
        other = self.w(self.src / "Other.nes", nes_rom())
        code, _ = self.run_push(str(self.rom), str(other))
        self.assertEqual(code, 1)
        self.assertEqual(sorted(p.name for p in (self.sd2 / "Import").iterdir()), ["game.gba"])
        self.assertEqual((self.sd2 / "Import" / "game.gba").read_bytes(), b"old")

    def test_not_enough_space(self):
        code, _ = self.run_push(str(self.rom), free=1 * MB)
        self.assertEqual(code, 1)
        self.assertEqual(list((self.sd2 / "Import").iterdir()), [])

    def test_directory_pushed_recursively(self):
        d = self.src / "Disc Game"
        self.w(d / "G.cue", b'FILE "G.bin" BINARY\r\n')
        self.w(d / "sub" / "G.bin", psx_bin())
        code, _ = self.run_push(str(d))
        self.assertEqual(code, 0)
        self.assertTrue((self.sd2 / "Import" / "Disc Game" / "sub" / "G.bin").is_file())

    def test_unsafe_names_and_missing_files(self):
        self.assertEqual(self.P.main([str(self.src / "nope.gba")], remote=FakeRemote(self.sd1, self.sd2, self.ud)), 3)
        hidden = self.w(self.src / ".hidden.gba", gba_rom())
        self.assertEqual(self.P.main([str(hidden)], remote=FakeRemote(self.sd1, self.sd2, self.ud)), 3)

    def test_apply_without_yes_only_plans(self):
        code, _ = self.run_push(str(self.rom), "--apply")
        self.assertEqual(code, 0)
        self.assertEqual(list((self.sd2 / "Roms").rglob("*")), [])
        self.assertTrue((self.sd2 / "Import" / "Game.gba").exists())

    def test_apply_yes_installs(self):
        code, _ = self.run_push(str(self.rom), "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual((self.sd2 / "Roms" / "GBA" / "Game.gba").read_bytes(), gba_rom())
        self.assertTrue((self.sd2 / "Import" / ".done" / "Game.gba").exists())

    def test_apply_yes_only_touches_pushed_files(self):
        self.w(self.sd2 / "Import" / "Other.nes", nes_rom())          # đã nằm sẵn từ trước, KHÔNG phải thứ vừa đẩy
        code, _ = self.run_push(str(self.rom), "--apply", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue((self.sd2 / "Roms" / "GBA" / "Game.gba").is_file())
        self.assertFalse((self.sd2 / "Roms" / "NES").exists())
        self.assertTrue((self.sd2 / "Import" / "Other.nes").exists())

    def test_rmtree_guard(self):
        with self.assertRaises(self.P.PushError):
            self.P.SshRemote(None).rmtree("/mnt/sdcard/Roms")

    def test_flags_need_apply(self):
        self.assertEqual(self.P.main([str(self.rom), "--yes"], remote=FakeRemote(self.sd1, self.sd2, self.ud)), 3)


if __name__ == "__main__":
    unittest.main()
