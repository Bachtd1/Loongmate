"""Patch ROM (engine/patcher.py): hành vi thật.

Vector IPS/UPS/BPS dựng TAY từ đặc tả (từng byte được ghi ra và giải thích), không dùng bộ mã hoá phản chiếu bộ giải mã. Chỉ có
CRC32 (zlib) tính trong test. Phần còn lại: thẻ giả + importer thật, CLI qua CLI.main.

Chứng minh test biết đỏ (làm hỏng tạm rồi chạy lại): (a) xử lý chồng lấn của TargetCopy BPS, (b) kiểm CRC ROM nguồn,
(c) chốt chặn "ROM phải nằm dưới Roms/" và "không ghi đè bản gốc".
"""
import contextlib
import hashlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.libsource as LS  # noqa: E402
import engine.patcher as PT  # noqa: E402
import engine.cli as CLI  # noqa: E402
from engine.libsource import LibError  # noqa: E402


def crc(b: bytes) -> int:
    return zlib.crc32(b) & 0xFFFFFFFF


def le32(n: int) -> bytes:
    return n.to_bytes(4, "little")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def seal(body: bytes) -> bytes:
    """Thêm CRC32 của chính patch (đúng đặc tả UPS/BPS: CRC của mọi byte trừ 4 byte cuối)."""
    return body + le32(crc(body))


def refix(patch: bytes) -> bytes:
    """Tính lại CRC của patch sau khi sửa thân (để fuzz đi sâu vào bộ phân tích thay vì bị CRC chặn ngay)."""
    return seal(patch[:-4])


# ---------------------------------------------------------------- IPS (vector tay)
IPS_SRC = bytes(range(16))
IPS_NORMAL = b"PATCH" + b"\x00\x00\x02" + b"\x00\x03" + b"ABC" + b"EOF"             # ghi "ABC" tại offset 2
IPS_RLE = b"PATCH" + b"\x00\x00\x08" + b"\x00\x00" + b"\x00\x04" + b"\xff" + b"EOF"  # size 0 -> RLE: 4 lần 0xFF tại offset 8
IPS_TRUNC = IPS_NORMAL + b"\x00\x00\x0a"                                            # sau EOF: cắt còn 10 byte
IPS_GROW = (b"PATCH" + b"\x00\x00\x10" + b"\x00\x02" + b"ZZ"                        # offset 16 (đúng cuối ROM) -> dài thêm
            + b"\x00\x00\x14" + b"\x00\x01" + b"Y" + b"EOF")                        # offset 20: khoảng 18..19 điền 0

# ---------------------------------------------------------------- UPS (vector tay)
UPS_SRC = b"ABCDEFGH"
UPS_TGT = b"ABBFEF\xb8H"
# "UPS1", nguồn 8 byte (0x88), đích 8 byte (0x88);
# hunk 1: bỏ 2 (0x82), XOR "\x01\x02" (C^1=B, D^2=F), kết thúc 0x00 (vị trí kết thúc cũng tiến 1 => con trỏ = 5);
# hunk 2: bỏ 1 (0x81) => 6, XOR "\xff" (G^ff=b8), kết thúc 0x00.
UPS_BODY = b"UPS1" + b"\x88" + b"\x88" + b"\x82" + b"\x01\x02\x00" + b"\x81" + b"\xff\x00" + le32(crc(UPS_SRC)) + le32(crc(UPS_TGT))
UPS_PATCH = seal(UPS_BODY)
# đích dài hơn nguồn (10 byte): nguồn 0x88, đích 0x8a; bỏ 8 (0x88), XOR "\x7a\x7b" lên phần zero-fill
UPS_GROW_TGT = UPS_SRC + b"\x7a\x7b"
UPS_GROW = seal(b"UPS1" + b"\x88" + b"\x8a" + b"\x88" + b"\x7a\x7b\x00" + le32(crc(UPS_SRC)) + le32(crc(UPS_GROW_TGT)))
# nguồn 128 byte: varint 128 = bytes 00 80 (x=0: data=0, shift=128, data+=128; x=0x80: dừng) -> kiểm bộ giải mã varint nhiều byte
UPS_128_SRC = bytes(range(128))
UPS_128_TGT = bytes([0x55]) + UPS_128_SRC[1:]
UPS_128 = seal(b"UPS1" + b"\x00\x80" + b"\x00\x80" + b"\x80" + b"\x55\x00"  # bỏ 0; byte0 ^ 0x55 (0^0x55=0x55); XOR byte 0x55 != 0
                + le32(crc(UPS_128_SRC)) + le32(crc(UPS_128_TGT)))

# ---------------------------------------------------------------- BPS (vector tay)
BPS_SRC = b"0123456789"
# action = (độ dài-1)<<2 | lệnh; varint 1 byte = 0x80 | giá trị (giá trị < 128):
#  SourceRead 3            : ((3-1)<<2)|0 = 8            -> 88                 => "012"
#  TargetRead 2 "ab"       : ((2-1)<<2)|1 = 5            -> 85 'a' 'b'         => "012ab"
#  SourceCopy 4, offset +5 : ((4-1)<<2)|2 = 14 -> 8e ; d=(5<<1)|0=10 -> 8a     => src[5:9]="5678", srcRel=9
#  TargetCopy 5, offset +7 : ((5-1)<<2)|3 = 19 -> 93 ; d=(7<<1)=14 -> 8e       => đã có 9 byte, từ [7:]="78" (dist 2) lặp chồng lấn "78787"
#  SourceCopy 2, offset -8 : ((2-1)<<2)|2 = 6  -> 86 ; d=(8<<1)|1=17 -> 91      => srcRel 9-8=1 -> "12"
#  TargetCopy 2, offset -12: ((2-1)<<2)|3 = 7  -> 87 ; d=(12<<1)|1=25 -> 99     => tgtRel 7+5-12=0 -> "01"
BPS_TGT = b"012" + b"ab" + b"5678" + b"78787" + b"12" + b"01"
BPS_META = b"<m>hi</m>"
BPS_BODY = (b"BPS1" + b"\x8a" + b"\x92" + b"\x89" + BPS_META                      # nguồn 10, đích 18, metadata 9 byte
            + b"\x88" + b"\x85ab" + b"\x8e\x8a" + b"\x93\x8e" + b"\x86\x91" + b"\x87\x99"
            + le32(crc(BPS_SRC)) + le32(crc(BPS_TGT)))
BPS_PATCH = seal(BPS_BODY)


def make_zip(path: Path, entries) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-patch-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir()
        self.env = LS.Env(self.ud, {"primary": self.sd1, "secondary": self.sd2}, self.tmp / "secrets", proxy=None)

    def rom(self, card, system, name, data):
        p = (self.sd1 if card == 1 else self.sd2) / "Roms" / system / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def src_file(self, name, data):
        p = self.tmp / "incoming" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(data)
        return p

    def store(self, name, data):
        return PT.store_patch_files(self.ud, self.src_file(name, data))[0]

    def store_files(self):
        d = self.ud / "patches"
        return sorted(os.listdir(d)) if d.is_dir() else []


# ======================================================================================
# giải mã
# ======================================================================================
class TestIps(unittest.TestCase):
    def run_ips(self, patch, src=IPS_SRC):
        out, verified = PT.apply_bytes(patch, src)
        self.assertFalse(verified)
        return out

    def test_normal_record(self):
        self.assertEqual(self.run_ips(IPS_NORMAL), b"\x00\x01ABC\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f")

    def test_rle_record(self):
        self.assertEqual(self.run_ips(IPS_RLE), IPS_SRC[:8] + b"\xff\xff\xff\xff" + IPS_SRC[12:])

    def test_eof_then_truncate(self):
        self.assertEqual(self.run_ips(IPS_TRUNC), b"\x00\x01ABC\x05\x06\x07\x08\x09")

    def test_record_extends_file_and_gap_is_zero_filled(self):
        self.assertEqual(self.run_ips(IPS_GROW), IPS_SRC + b"ZZ" + b"\x00\x00" + b"Y")

    def test_trailing_garbage_after_eof_is_refused(self):
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(IPS_NORMAL + b"\x01", IPS_SRC)
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertIn("Patch hỏng", cm.exception.message)

    def test_info(self):
        i = PT.info_bytes(IPS_RLE)
        self.assertEqual((i["format"], i["size"], i["source_crc32"], i["source_size"], i["target_size"], i["metadata"]),
                         ("ips", len(IPS_RLE), None, None, None, ""))


class TestUps(unittest.TestCase):
    def test_skip_and_xor_runs(self):
        out, verified = PT.apply_bytes(UPS_PATCH, UPS_SRC)
        self.assertEqual(out, UPS_TGT)
        self.assertTrue(verified)

    def test_output_larger_than_source(self):
        self.assertEqual(PT.apply_bytes(UPS_GROW, UPS_SRC)[0], UPS_GROW_TGT)

    def test_multibyte_varint_sizes(self):
        self.assertEqual(PT.apply_bytes(UPS_128, UPS_128_SRC)[0], UPS_128_TGT)
        i = PT.info_bytes(UPS_128)
        self.assertEqual((i["source_size"], i["target_size"]), (128, 128))

    def test_wrong_source_crc_is_mismatch_with_exact_message(self):
        other = b"ABCDEFGX"                                                   # cùng kích thước, khác nội dung
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(UPS_PATCH, other)
        self.assertEqual(cm.exception.kind, "mismatch")
        self.assertEqual(cm.exception.message, "Patch này dành cho bản ROM khác (cần CRC32 %08X, 8 byte; ROM đã chọn là %08X, 8 byte)."
                         % (crc(UPS_SRC), crc(other)))

    def test_wrong_source_size_is_mismatch(self):
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(UPS_PATCH, UPS_SRC + b"!")
        self.assertEqual(cm.exception.kind, "mismatch")

    def test_wrong_patch_crc_is_invalid(self):
        bad = bytearray(UPS_PATCH)
        bad[6] ^= 0x01                                                       # sửa thân, giữ nguyên CRC cũ
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(bytes(bad), UPS_SRC)
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertIn("CRC32 của chính file patch", cm.exception.message)

    def test_wrong_target_crc_is_refused(self):
        bad = seal(UPS_BODY[:-4] + le32(crc(UPS_TGT) ^ 1))                   # CRC patch đúng, CRC đích sai
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(bad, UPS_SRC)
        self.assertEqual(cm.exception.kind, "mismatch")

    def test_info(self):
        i = PT.info_bytes(UPS_PATCH)
        self.assertEqual((i["format"], i["source_size"], i["target_size"], i["source_crc32"]), ("ups", 8, 8, "%08x" % crc(UPS_SRC)))


class TestBps(unittest.TestCase):
    def test_all_four_actions_overlap_and_negative_offsets(self):
        out, verified = PT.apply_bytes(BPS_PATCH, BPS_SRC)
        self.assertEqual(out, b"012ab5678787871201")
        self.assertEqual(out, BPS_TGT)
        self.assertTrue(verified)

    def test_overlapping_target_copy_repeats_with_period(self):
        # tách riêng phần chồng lấn: "78787" phải là chu kỳ 2 ("78" lặp), không phải chép một lần rồi cắt
        out = PT.apply_bytes(BPS_PATCH, BPS_SRC)[0]
        self.assertEqual(out[9:14], b"78787")

    def test_metadata_read(self):
        i = PT.info_bytes(BPS_PATCH)
        self.assertEqual((i["format"], i["metadata"], i["source_size"], i["target_size"], i["source_crc32"]),
                         ("bps", "<m>hi</m>", 10, 18, "%08x" % crc(BPS_SRC)))

    def test_metadata_capped_and_decoded_leniently(self):
        meta = bytes([0xFF]) * 700                                            # không phải UTF-8 hợp lệ -> thay bằng U+FFFD
        meta_size_varint = bytes([0x3C, 0x84])   # 700: x=0x3c -> data=60, shift=128, data+=128 -> 188; x=0x84 -> data += 4*128 = 700, dừng
        body = b"BPS1" + bytes([0x8A]) + bytes([0x81]) + meta_size_varint + meta + bytes([0x80]) + le32(crc(BPS_SRC)) + le32(crc(b"0"))
        i = PT.info_bytes(seal(body))
        self.assertEqual(len(i["metadata"]), 500)
        self.assertEqual(i["metadata"][0], chr(0xFFFD))

    def test_wrong_source_is_mismatch(self):
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(BPS_PATCH, b"012345678X")
        self.assertEqual(cm.exception.kind, "mismatch")
        self.assertIn("Patch này dành cho bản ROM khác", cm.exception.message)

    def test_wrong_patch_crc_is_invalid(self):
        bad = bytearray(BPS_PATCH)
        bad[-1] ^= 0xFF
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(bytes(bad), BPS_SRC)
        self.assertEqual(cm.exception.kind, "invalid")

    def test_wrong_target_crc_is_refused_and_returns_nothing(self):
        bad = seal(BPS_BODY[:-4] + le32(crc(BPS_TGT) ^ 0x10))
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(bad, BPS_SRC)
        self.assertEqual(cm.exception.kind, "mismatch")

    def test_action_beyond_target_size_is_invalid(self):
        # đích khai 4 byte nhưng action SourceRead 10 byte ((10-1)<<2 = 36 -> 0xa4)
        body = b"BPS1" + b"\x8a" + b"\x84" + b"\x80" + b"\xa4" + le32(crc(BPS_SRC)) + le32(crc(b"0123"))
        with self.assertRaises(LibError) as cm:
            PT.apply_bytes(seal(body), BPS_SRC)
        self.assertEqual(cm.exception.kind, "invalid")

    def test_target_copy_before_any_output_is_invalid(self):
        body = b"BPS1" + b"\x8a" + b"\x82" + b"\x80" + b"\x87\x80" + le32(crc(BPS_SRC)) + le32(0)   # TargetCopy 2 byte, offset 0, out rỗng
        with self.assertRaises(LibError):
            PT.apply_bytes(seal(body), BPS_SRC)


class TestLimits(unittest.TestCase):
    def test_not_a_patch(self):
        for junk in (b"", b"PAT", b"HELLO WORLD", b"\x00" * 40):
            with self.assertRaises(LibError) as cm:
                PT.apply_bytes(junk, b"x")
            self.assertEqual(cm.exception.kind, "invalid")

    def test_huge_declared_size_refused_before_allocating(self):
        # nguồn/đích khai 2^40 byte (varint nhiều byte) -> từ chối ngay, không cấp phát
        big = b"\x00" * 5 + b"\x80"                                          # giá trị rất lớn
        for body in (b"UPS1" + big + big, b"BPS1" + big + big + b"\x80"):
            with self.assertRaises(LibError):
                PT.apply_bytes(seal(body + le32(0) + le32(0)), b"x")

    def test_oversize_inputs(self):
        with mock.patch.object(PT, "MAX_ROM_BYTES", 8):
            with self.assertRaises(LibError) as cm:
                PT.apply_bytes(IPS_NORMAL, b"x" * 9)
            self.assertEqual(cm.exception.kind, "too_large")
        with mock.patch.object(PT, "MAX_PATCH_BYTES", 8):
            with self.assertRaises(LibError):
                PT.apply_bytes(IPS_NORMAL, IPS_SRC)


class TestFuzz(unittest.TestCase):
    """Patch hỏng chỉ được sinh LibError — không bao giờ IndexError/struct.error/MemoryError..."""
    VALID = [(IPS_NORMAL, IPS_SRC), (IPS_RLE, IPS_SRC), (IPS_TRUNC, IPS_SRC), (IPS_GROW, IPS_SRC), (UPS_PATCH, UPS_SRC),
             (UPS_GROW, UPS_SRC), (UPS_128, UPS_128_SRC), (BPS_PATCH, BPS_SRC)]

    def attempt(self, patch, src):
        try:
            PT.apply_bytes(patch, src)
        except LibError:
            pass
        try:
            PT.info_bytes(patch)
        except LibError:
            pass

    def test_every_prefix(self):
        for patch, src in self.VALID:
            for n in range(len(patch)):
                self.attempt(patch[:n], src)
                if patch[:4] in (b"UPS1", b"BPS1") and n > 8:
                    self.attempt(refix(patch[:n] + b"\x00\x00\x00\x00") if n >= 4 else patch[:n], src)   # cắt thân nhưng CRC vẫn đúng

    def test_every_single_byte_flip_with_valid_crc(self):
        for patch, src in self.VALID:
            for i in range(len(patch)):
                for v in (0x00, 0x01, 0x7F, 0x80, 0xFF):
                    m = bytearray(patch)
                    m[i] = v
                    m = bytes(m)
                    self.attempt(refix(m) if patch[:4] in (b"UPS1", b"BPS1") else m, src)

    def test_random_bytes_with_each_magic(self):
        rnd = random.Random(1234)
        for magic in (b"PATCH", b"UPS1", b"BPS1"):
            for _ in range(400):
                body = magic + bytes(rnd.randrange(256) for _ in range(rnd.randrange(0, 60)))
                self.attempt(refix(body + b"\0\0\0\0") if magic != b"PATCH" else body, bytes(rnd.randrange(256) for _ in range(10)))

    def test_trailing_and_inserted_bytes(self):
        for patch, src in self.VALID:
            for extra in (b"\x00", b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff"):
                self.attempt(patch + extra, src)
                self.attempt(refix(patch[:-4] + extra + patch[-4:]) if patch[:4] in (b"UPS1", b"BPS1") else patch + extra, src)


# ======================================================================================
# kho patch, liệt kê, zip
# ======================================================================================
class TestStore(Base):
    def test_single_patch_copied_and_original_kept(self):
        src = self.src_file("My Hack.ips", IPS_NORMAL)
        names = PT.store_patch_files(self.ud, src)
        self.assertEqual(names, ["My Hack.ips"])
        self.assertEqual((self.ud / "patches" / "My Hack.ips").read_bytes(), IPS_NORMAL)
        self.assertTrue(src.exists())

    def test_identical_reused_different_gets_suffix(self):
        a = self.store("fix.ips", IPS_NORMAL)
        self.assertEqual(self.store("fix.ips", IPS_NORMAL), a)
        b = self.store("fix.ips", IPS_RLE)
        self.assertEqual((a, b), ("fix.ips", "fix (2).ips"))
        self.assertEqual(self.store("fix.ips", IPS_RLE), "fix (2).ips")
        self.assertEqual(self.store_files(), ["fix (2).ips", "fix.ips"])

    def test_name_is_fat32_safe(self):
        n = PT._put_in_store(self.ud, 'a:b"c?.ips', IPS_NORMAL)         # (Windows không cho tạo file nguồn tên thế này)
        self.assertNotRegex(n, r'[<>:"|?*]')

    def test_not_a_patch_or_wrong_type_refused(self):
        with self.assertRaises(LibError):
            PT.store_patch_files(self.ud, self.src_file("x.ips", b"hello"))
        with self.assertRaises(LibError):
            PT.store_patch_files(self.ud, self.src_file("x.txt", IPS_NORMAL))
        self.assertEqual(self.store_files(), [])

    def test_zip_extracts_only_patches(self):
        z = make_zip(self.tmp / "p.zip", [("readme.txt", b"hi"), ("sub/A.bps", BPS_PATCH), ("B.ups", UPS_PATCH), ("cover.png", b"x")])
        self.assertEqual(sorted(PT.store_patch_files(self.ud, z)), ["A.bps", "B.ups"])
        self.assertEqual(self.store_files(), ["A.bps", "B.ups"])

    def test_zip_without_patch_is_invalid(self):
        z = make_zip(self.tmp / "p.zip", [("readme.txt", b"hi")])
        with self.assertRaises(LibError) as cm:
            PT.store_patch_files(self.ud, z)
        self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(self.store_files(), [])

    def test_zip_traversal_refused_and_nothing_written(self):
        z = make_zip(self.tmp / "p.zip", [("../evil.ips", IPS_NORMAL), ("ok.ips", IPS_NORMAL)])
        with self.assertRaises(LibError) as cm:
            PT.store_patch_files(self.ud, z)
        self.assertIn("..", cm.exception.message)
        self.assertEqual(self.store_files(), [])
        self.assertFalse((self.ud / "evil.ips").exists())

    def test_zip_bomb_refused(self):
        z = make_zip(self.tmp / "p.zip", [("bomb.ips", b"PATCH" + b"\x00" * (8 * 1024 * 1024))])
        with self.assertRaises(LibError) as cm:
            PT.store_patch_files(self.ud, z)
        self.assertIn("zip bomb", cm.exception.message)
        self.assertEqual(self.store_files(), [])

    def test_zip_with_executable_refused(self):
        z = make_zip(self.tmp / "p.zip", [("run.exe", b"MZ"), ("ok.ips", IPS_NORMAL)])
        with self.assertRaises(LibError):
            PT.store_patch_files(self.ud, z)
        self.assertEqual(self.store_files(), [])

    def test_zip_too_many_patches(self):
        z = make_zip(self.tmp / "p.zip", [("p%d.ips" % i, IPS_NORMAL) for i in range(21)])
        with self.assertRaises(LibError) as cm:
            PT.store_patch_files(self.ud, z)
        self.assertIn("quá nhiều", cm.exception.message)

    def test_zip_member_that_is_not_a_patch_refused(self):
        z = make_zip(self.tmp / "p.zip", [("a.ips", b"garbage")])
        with self.assertRaises(LibError):
            PT.store_patch_files(self.ud, z)

    def test_zip_patch_over_size_limit_refused(self):
        z = make_zip(self.tmp / "p.zip", [("a.ips", IPS_NORMAL)])
        with mock.patch.object(PT, "MAX_PATCH_BYTES", 8):
            with self.assertRaises(LibError):
                PT.store_patch_files(self.ud, z)

    def test_zip_is_patch_only_classification(self):
        make_zip(self.tmp / "a.zip", [("x.ips", IPS_NORMAL), ("readme.txt", b"hi")])
        make_zip(self.tmp / "b.zip", [("x.ips", IPS_NORMAL), ("game.gba", b"rom")])
        make_zip(self.tmp / "c.zip", [("game.gba", b"rom")])
        (self.tmp / "d.zip").write_bytes(b"not a zip")
        self.assertEqual([PT._zip_is_patch_only(self.tmp / n) for n in ("a.zip", "b.zip", "c.zip", "d.zip")], [True, False, False, False])


class TestList(Base):
    def test_store_and_import_sources_with_ids_and_errors(self):
        self.store("fix.bps", BPS_PATCH)
        self.store("plain.ips", IPS_NORMAL)
        imp = self.sd2 / "Import"
        (imp / "deep").mkdir(parents=True)
        (imp / "up.ups").write_bytes(UPS_PATCH)
        (imp / "broken.ips").write_bytes(b"nonsense")
        (imp / "deep" / "hidden.ips").write_bytes(IPS_NORMAL)       # không đệ quy
        (imp / "game.gba").write_bytes(b"rom")
        got = {p["id"]: p for p in PT.list_patches(self.env)}
        self.assertEqual(sorted(got), ["i:secondary:broken.ips", "i:secondary:up.ups", "s:fix.bps", "s:plain.ips"])
        b = got["s:fix.bps"]
        self.assertEqual((b["name"], b["format"], b["where"], b["verifies"], b["source_size"], b["source_crc32"], b["metadata"], b["size"]),
                         ("fix.bps", "bps", "store", True, 10, "%08x" % crc(BPS_SRC), "<m>hi</m>", len(BPS_PATCH)))
        self.assertNotIn("error", b)
        self.assertNotIn("card", b)
        i = got["i:secondary:up.ups"]
        self.assertEqual((i["where"], i["card"], i["verifies"], i["path"]), ("import", "secondary", True, str(imp / "up.ups")))
        self.assertFalse(got["s:plain.ips"]["verifies"])
        bad = got["i:secondary:broken.ips"]
        self.assertIn("Patch hỏng", bad["error"])
        self.assertEqual(bad["format"], "ips")

    def test_empty(self):
        self.assertEqual(PT.list_patches(self.env), [])

    def test_delete_store_only(self):
        self.store("fix.bps", BPS_PATCH)
        (self.sd1 / "Import").mkdir()
        (self.sd1 / "Import" / "imp.ips").write_bytes(IPS_NORMAL)
        r = PT.delete_patch(self.env, "s:fix.bps")
        self.assertTrue(r["success"])
        self.assertEqual(self.store_files(), [])
        with self.assertRaises(LibError) as cm:
            PT.delete_patch(self.env, "i:primary:imp.ips")
        self.assertEqual(cm.exception.message, "patch nằm trong Import/: xoá bằng tay hoặc dùng Nhập game")
        self.assertTrue((self.sd1 / "Import" / "imp.ips").exists())
        with self.assertRaises(LibError) as cm:
            PT.delete_patch(self.env, "s:../../etc/passwd")
        self.assertEqual(cm.exception.kind, "not_found")


# ======================================================================================
# tìm ROM khớp
# ======================================================================================
class TestCandidates(Base):
    def setUp(self):
        super().setUp()
        self.pid = "s:" + self.store("fix.bps", BPS_PATCH)

    def test_crc_match_among_same_size_decoys_across_both_cards(self):
        self.rom(1, "GBA", "decoy1.gba", b"012345678X")             # cùng kích thước, khác CRC
        self.rom(2, "GBA", "decoy2.gba", b"X123456789")
        want = self.rom(2, "SNES", "real.sfc", BPS_SRC)
        self.rom(1, "SNES", "other-size.sfc", BPS_SRC + b"0")
        self.rom(1, "NES", "also-real.nes", BPS_SRC)                # bản thứ hai ở thẻ kia
        r = PT.candidates(self.env, self.pid)
        self.assertEqual((r["verify"], r["source_size"], r["source_crc32"], r["success"]), ("crc", 10, "%08x" % crc(BPS_SRC), True))
        self.assertEqual(sorted((m["card"], m["system"], m["name"], m["size"], m["header_skipped"]) for m in r["matches"]),
                         [("primary", "NES", "also-real.nes", 10, False), ("secondary", "SNES", "real.sfc", 10, False)])
        self.assertIn(str(want), [m["path"] for m in r["matches"]])
        self.assertEqual(r["scanned"], 4)                            # chỉ băm file đúng kích thước (10 hoặc 522)
        self.assertFalse(r["truncated"])

    def test_snes_copier_header_match(self):
        self.rom(1, "SNES", "hdr.smc", bytes(512) + BPS_SRC)
        self.rom(1, "SNES", "decoy.smc", bytes(512) + b"012345678X")
        r = PT.candidates(self.env, self.pid)
        self.assertEqual([(m["name"], m["header_skipped"]) for m in r["matches"]], [("hdr.smc", True)])

    def test_system_filter_and_no_match(self):
        self.rom(1, "SNES", "real.sfc", BPS_SRC)
        self.assertEqual(PT.candidates(self.env, self.pid, "GBA")["matches"], [])
        self.assertEqual(len(PT.candidates(self.env, self.pid, "snes")["matches"]), 1)

    def test_images_and_non_rom_files_ignored(self):
        self.rom(1, "SNES", "Imgs/cover.sfc", BPS_SRC)
        self.rom(1, "SNES", "real.png", BPS_SRC)
        self.assertEqual(PT.candidates(self.env, self.pid)["matches"], [])

    def test_crc_budget_caps_work_and_reports_it(self):
        self.rom(1, "SNES", "a.sfc", b"012345678X")
        self.rom(1, "SNES", "b.sfc", BPS_SRC)
        with mock.patch.object(PT, "MAX_CRC_BYTES", 10):
            r = PT.candidates(self.env, self.pid)
        self.assertEqual((r["scanned"], r["truncated"]), (1, True))

    def test_ips_lists_systems_then_roms_sorted(self):
        ips = "s:" + self.store("plain.ips", IPS_NORMAL)
        self.rom(1, "GBA", "b.gba", b"1")
        self.rom(2, "GBA", "A.gba", b"2")
        self.rom(1, "SNES", "z.sfc", b"3")
        self.rom(1, "SNES", "Imgs/x.png", b"4")
        r = PT.candidates(self.env, ips)
        self.assertEqual((r["verify"], r["systems"]), ("none", [{"system": "GBA", "count": 2}, {"system": "SNES", "count": 1}]))
        r = PT.candidates(self.env, ips, "GBA")
        self.assertEqual(r["verify"], "none")
        self.assertEqual([(m["name"], m["card"], m["system"], m["size"]) for m in r["roms"]], [("A.gba", "secondary", "GBA", 1), ("b.gba", "primary", "GBA", 1)])
        self.assertTrue(all(Path(m["path"]).is_file() for m in r["roms"]))

    def test_broken_patch_cannot_be_used(self):
        (self.sd1 / "Import").mkdir()
        (self.sd1 / "Import" / "bad.bps").write_bytes(b"junk")
        with self.assertRaises(LibError) as cm:
            PT.candidates(self.env, "i:primary:bad.bps")
        self.assertIn("Patch hỏng", cm.exception.message)


# ======================================================================================
# áp patch
# ======================================================================================
class TestApply(Base):
    def setUp(self):
        super().setUp()
        self.pid = "s:" + self.store("fix.bps", BPS_PATCH)
        self.orig = self.rom(2, "SNES", "Game.sfc", BPS_SRC)
        self.orig_hash = sha(BPS_SRC)

    def files(self, d=None):
        return sorted(os.listdir(d or self.orig.parent))

    def test_bps_writes_new_file_beside_original_and_never_touches_it(self):
        mtime = self.orig.stat().st_mtime_ns
        r = PT.apply(self.env, self.pid, self.orig)
        out = Path(r["output"])
        self.assertEqual((out.parent, r["name"]), (self.orig.parent, "Game [fix].sfc"))
        self.assertEqual(out.read_bytes(), BPS_TGT)
        self.assertEqual((r["success"], r["size"], r["sha256"], r["verified"], r["already"], r["warning"]), (True, 18, sha(BPS_TGT), True, False, None))
        self.assertEqual(sha(self.orig.read_bytes()), self.orig_hash)         # bản gốc giống hệt từng byte
        self.assertEqual(self.orig.stat().st_mtime_ns, mtime)
        self.assertEqual(self.files(), ["Game [fix].sfc", "Game.sfc"])         # không để lại file tạm
        led = (self.ud / "library-ledger.jsonl").read_text(encoding="utf-8")
        self.assertIn("patch_applied", led)

    def test_second_run_is_already_and_writes_nothing(self):
        a = PT.apply(self.env, self.pid, self.orig)
        mt = Path(a["output"]).stat().st_mtime_ns
        b = PT.apply(self.env, self.pid, self.orig)
        self.assertEqual((b["already"], b["output"], b["changed"]), (True, a["output"], False))
        self.assertEqual(Path(b["output"]).stat().st_mtime_ns, mt)
        self.assertEqual(len(self.files()), 2)

    def test_name_collision_with_different_content_gets_counter(self):
        (self.orig.parent / "Game [fix].sfc").write_bytes(b"something else")
        r = PT.apply(self.env, self.pid, self.orig)
        self.assertEqual(r["name"], "Game [fix] (2).sfc")
        self.assertEqual((self.orig.parent / "Game [fix].sfc").read_bytes(), b"something else")      # file khác không bị đè
        (self.orig.parent / "Game [fix] (2).sfc").write_bytes(b"x")
        os.remove(self.orig.parent / "Game [fix] (2).sfc")
        (self.orig.parent / "Game [fix] (2).sfc").write_bytes(b"y")
        self.assertEqual(PT.apply(self.env, self.pid, self.orig)["name"], "Game [fix] (3).sfc")

    def test_collision_is_case_insensitive(self):
        (self.orig.parent / "GAME [FIX].SFC").write_bytes(b"other")
        self.assertEqual(PT.apply(self.env, self.pid, self.orig)["name"], "Game [fix] (2).sfc")

    def test_custom_name(self):
        r = PT.apply(self.env, self.pid, self.orig, out_name="Hack: Edition?.sfc")
        self.assertEqual(Path(r["output"]).parent, self.orig.parent)
        self.assertNotRegex(r["name"], r'[<>:"|?*]')
        self.assertTrue(r["name"].endswith(".sfc"))
        r = PT.apply(self.env, self.pid, self.orig, out_name="../../escape")
        self.assertEqual(Path(r["output"]).parent, self.orig.parent)
        self.assertEqual(Path(r["output"]).suffix, ".sfc")                    # không có đuôi -> lấy đuôi ROM gốc

    def test_custom_name_equal_to_original_never_overwrites_it(self):
        r = PT.apply(self.env, self.pid, self.orig, out_name="Game.sfc")
        self.assertEqual(r["name"], "Game (2).sfc")
        self.assertEqual(sha(self.orig.read_bytes()), self.orig_hash)

    def test_name_limit(self):
        long = self.rom(2, "SNES", "L" * 200 + ".sfc", BPS_SRC)
        r = PT.apply(self.env, self.pid, long)
        self.assertLessEqual(len(r["name"]), 120)
        self.assertTrue(r["name"].endswith("[fix].sfc"))

    def test_rom_outside_roms_refused(self):
        outside = self.src_file("Game.sfc", BPS_SRC)
        for bad in (outside, self.sd2 / "Import" / "x.sfc", self.sd2 / "Roms" / ".." / "Game.sfc", self.sd2 / "Roms", self.sd2 / "Roms" / "SNES"):
            with self.assertRaises(LibError, msg=str(bad)) as cm:
                PT.apply(self.env, self.pid, bad)
            self.assertEqual(cm.exception.kind, "invalid")
        self.assertEqual(sorted(os.listdir(outside.parent)), ["Game.sfc", "fix.bps"])    # không tạo gì cạnh file ngoài Roms/
        self.assertEqual(self.files(), ["Game.sfc"])

    def test_sibling_dir_with_roms_prefix_is_not_roms(self):
        evil = self.sd2 / "Roms_evil" / "SNES" / "x.sfc"
        evil.parent.mkdir(parents=True)
        evil.write_bytes(BPS_SRC)
        with self.assertRaises(LibError):
            PT.apply(self.env, self.pid, evil)

    def test_wrong_rom_is_mismatch_and_writes_nothing(self):
        wrong = self.rom(2, "SNES", "Other.sfc", b"012345678X")
        with self.assertRaises(LibError) as cm:
            PT.apply(self.env, self.pid, wrong)
        self.assertEqual(cm.exception.kind, "mismatch")
        self.assertEqual(self.files(), ["Game.sfc", "Other.sfc"])

    def test_header_skipped_keeps_header_in_output(self):
        hdr = bytes(range(200, 255)) * 10
        hdr = hdr[:512]
        rom = self.rom(1, "SNES", "Hdr.smc", hdr + BPS_SRC)
        r = PT.apply(self.env, self.pid, rom, header_skipped=True)
        self.assertEqual(Path(r["output"]).read_bytes(), hdr + BPS_TGT)
        self.assertEqual(rom.read_bytes(), hdr + BPS_SRC)
        with self.assertRaises(LibError) as cm:                              # không bỏ header thì CRC/kích thước lệch
            PT.apply(self.env, self.pid, rom)
        self.assertEqual(cm.exception.kind, "mismatch")

    def test_ips_apply_has_warning_and_is_unverified(self):
        pid = "s:" + self.store("plain.ips", IPS_NORMAL)
        rom = self.rom(1, "GBA", "G.gba", IPS_SRC)
        r = PT.apply(self.env, pid, rom)
        self.assertEqual((r["verified"], r["name"]), (False, "G [plain].gba"))
        self.assertEqual(r["warning"], "IPS không có mã kiểm tra: nếu đây không đúng bản ROM gốc, game có thể lỗi. "
                                       "Bản gốc không bị sửa — xoá file mới nếu không chạy.")
        self.assertEqual(Path(r["output"]).read_bytes(), b"\x00\x01ABC" + IPS_SRC[5:])
        self.assertEqual(rom.read_bytes(), IPS_SRC)

    def test_ups_apply_from_import_dir(self):
        (self.sd1 / "Import").mkdir()
        (self.sd1 / "Import" / "u.ups").write_bytes(UPS_PATCH)
        rom = self.rom(1, "GB", "U.gb", UPS_SRC)
        r = PT.apply(self.env, "i:primary:u.ups", rom)
        self.assertEqual((Path(r["output"]).read_bytes(), r["verified"]), (UPS_TGT, True))
        self.assertTrue((self.sd1 / "Import" / "u.ups").exists())             # patch ở Import/ chỉ đọc

    def test_oversize_rom_refused(self):
        pid = "s:" + self.store("plain.ips", IPS_NORMAL)                      # IPS không khai kích thước nguồn nên không bị chặn sớm hơn
        with mock.patch.object(PT, "MAX_ROM_BYTES", 4):
            with self.assertRaises(LibError) as cm:
                PT.apply(self.env, pid, self.orig)
        self.assertEqual(cm.exception.kind, "too_large")

    def test_write_failure_leaves_no_partial_file(self):
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(LibError) as cm:
                PT.apply(self.env, self.pid, self.orig)
        self.assertEqual(cm.exception.kind, "storage")
        self.assertEqual(self.files(), ["Game.sfc"])


# ======================================================================================
# CLI
# ======================================================================================
class TestCli(Base):
    def run_cli(self, *argv, stdin=""):
        base = ["--quiet", "--no-rescan", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(stdin)), contextlib.redirect_stdout(out):
            code = CLI.main(base + list(argv))
        return code, json.loads(out.getvalue())

    def test_store_list_info_candidates_apply_delete(self):
        code, r = self.run_cli("patch", "store", str(self.src_file("fix.bps", BPS_PATCH)))
        self.assertEqual((code, r["stored"]), (0, ["fix.bps"]))
        code, r = self.run_cli("patch", "list")
        self.assertEqual((code, r["success"], [p["id"] for p in r["patches"]]), (0, True, ["s:fix.bps"]))
        code, r = self.run_cli("patch", "info", "s:fix.bps")
        self.assertEqual((code, r["format"], r["source_size"]), (0, "bps", 10))
        rom = self.rom(2, "SNES", "Game.sfc", BPS_SRC)
        code, r = self.run_cli("patch", "candidates", "s:fix.bps")
        self.assertEqual((code, r["verify"], [m["name"] for m in r["matches"]]), (0, "crc", ["Game.sfc"]))
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(rom))                  # thiếu --yes, không TTY
        self.assertNotEqual(code, 0)
        self.assertFalse(r["success"])
        self.assertEqual(sorted(os.listdir(rom.parent)), ["Game.sfc"])
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(rom), "--yes")
        self.assertEqual((code, r["success"], r["name"], r["verified"], r["already"]), (0, True, "Game [fix].sfc", True, False))
        self.assertIn("rescan", r)
        self.assertEqual((rom.parent / "Game [fix].sfc").read_bytes(), BPS_TGT)
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(rom), "--yes")
        self.assertEqual((code, r["already"], r["rescan"]), (0, True, None))                      # không đổi gì -> không rescan
        code, r = self.run_cli("patch", "delete", "s:fix.bps")
        self.assertNotEqual(code, 0)
        self.assertTrue(self.store_files())
        code, r = self.run_cli("patch", "delete", "s:fix.bps", "--yes")
        self.assertEqual((code, r["success"]), (0, True))
        self.assertEqual(self.store_files(), [])

    def test_errors_are_json_with_error_kind(self):
        code, r = self.run_cli("patch", "info", "s:nope.ips")
        self.assertEqual((code, r["success"], r["error_kind"]), (1, False, "not_found"))
        self.store("fix.bps", BPS_PATCH)
        outside = self.src_file("x.sfc", BPS_SRC)
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(outside), "--yes")
        self.assertEqual((code, r["error_kind"]), (1, "invalid"))
        wrong = self.rom(1, "SNES", "w.sfc", b"012345678X")
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(wrong), "--yes")
        self.assertEqual((code, r["error_kind"]), (1, "mismatch"))

    def test_ips_candidates_and_header_flag(self):
        self.store("plain.ips", IPS_NORMAL)
        self.rom(1, "GBA", "G.gba", IPS_SRC)
        code, r = self.run_cli("patch", "candidates", "s:plain.ips")
        self.assertEqual((code, r["systems"]), (0, [{"system": "GBA", "count": 1}]))
        code, r = self.run_cli("patch", "candidates", "s:plain.ips", "--system", "GBA")
        self.assertEqual([m["name"] for m in r["roms"]], ["G.gba"])
        hdr = bytes(512)
        rom = self.rom(1, "SNES", "H.smc", hdr + BPS_SRC)
        self.store("fix.bps", BPS_PATCH)
        code, r = self.run_cli("patch", "apply", "s:fix.bps", "--rom", str(rom), "--header-skipped", "--name", "Mine.smc", "--yes")
        self.assertEqual((code, r["name"]), (0, "Mine.smc"))
        self.assertEqual((rom.parent / "Mine.smc").read_bytes(), hdr + BPS_TGT)

    def test_bare_patch_is_usage_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = CLI.main(["--quiet", "--primary", str(self.sd1), "--userdata", str(self.ud), "--json", "patch"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
