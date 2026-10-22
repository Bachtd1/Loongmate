"""Giải nén archive ngoài .zip (engine/archive.py): .tar.*/.gz bằng thư viện chuẩn, .7z/.rar bằng 7-Zip đi kèm.
Mọi archive đều được kiểm như zip TRƯỚC khi ghi (đường dẫn, link, mật khẩu, kích thước, tỉ lệ nén), giải vào thư mục tạm
trên cùng thẻ rồi mới đổi tên thành Import/<tên>/; lỗi thì không để lại gì."""
import contextlib
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import archive as A  # noqa: E402
from engine import importer as IM  # noqa: E402


def add_bytes(t, name, data, **kw):
    ti = tarfile.TarInfo(name)
    ti.size = len(data)
    for k, v in kw.items():
        setattr(ti, k, v)
    t.addfile(ti, io.BytesIO(data))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_arch_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.card = self.tmp / "sd2"
        self.imp = self.card / "Import"
        self.imp.mkdir(parents=True)

    def leftovers(self):
        d = self.card / A.UNPACK_DIR
        return list(d.iterdir()) if d.exists() else []


class Unpack(Base):
    def test_tar_gz_extracts_to_import_folder_and_moves_archive_to_done(self):
        p = self.imp / "Pack.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            add_bytes(t, "Pack/GBA/Game.gba", b"\x00" * 300)
            add_bytes(t, "Pack/readme.txt", b"hi")
        r = A.unpack(p)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["files"], 2)
        self.assertTrue((self.imp / "Pack" / "Pack" / "GBA" / "Game.gba").is_file())
        self.assertFalse(p.exists())
        self.assertTrue((self.imp / ".done" / "Pack.tar.gz").is_file())
        self.assertEqual(self.leftovers(), [])

    def test_single_gz(self):
        p = self.imp / "Game.gba.gz"
        p.write_bytes(gzip.compress(b"ROM" * 100))
        r = A.unpack(p)
        self.assertTrue(r["success"], r)
        self.assertEqual((self.imp / "Game.gba" / "Game.gba").read_bytes(), b"ROM" * 100)

    def test_never_overwrites_existing_folder(self):
        (self.imp / "Pack").mkdir()
        p = self.imp / "Pack.tar"
        with tarfile.open(p, "w") as t:
            add_bytes(t, "a.gba", b"x")
        r = A.unpack(p)
        self.assertTrue(r["success"], r)
        self.assertTrue((self.imp / "Pack (2)" / "a.gba").is_file())

    def test_traversal_is_refused_and_nothing_written(self):
        p = self.imp / "Evil.tar"
        with tarfile.open(p, "w") as t:
            add_bytes(t, "../../escape.gba", b"x")
        r = A.unpack(p)
        self.assertFalse(r["success"])
        self.assertIn("..", r["message"])
        self.assertTrue(p.exists())
        self.assertFalse((self.tmp / "escape.gba").exists())
        self.assertEqual(self.leftovers(), [])

    def test_symlink_is_refused(self):
        p = self.imp / "Link.tar"
        with tarfile.open(p, "w") as t:
            ti = tarfile.TarInfo("evil")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            t.addfile(ti)
        r = A.unpack(p)
        self.assertFalse(r["success"])
        self.assertIn("symlink", r["message"])

    def test_bomb_ratio_is_refused(self):
        p = self.imp / "Bomb.tar.gz"
        with tarfile.open(p, "w:gz") as t:
            add_bytes(t, "zero.bin", b"\x00" * (8 * 1024 * 1024))
        r = A.unpack(p)
        self.assertFalse(r["success"])
        self.assertIn("zip bomb", r["message"])
        self.assertEqual(self.leftovers(), [])

    def test_not_enough_space_writes_nothing(self):
        p = self.imp / "Pack.tar"
        with tarfile.open(p, "w") as t:
            add_bytes(t, "a.gba", b"x" * 1000)
        with mock.patch.object(A.shutil, "disk_usage", return_value=mock.Mock(free=1000)):
            r = A.unpack(p)
        self.assertFalse(r["success"])
        self.assertIn("cần", r["message"])
        self.assertEqual(self.leftovers(), [])


class SevenZipListing(unittest.TestCase):
    SAMPLE = ("Path = Game/Game.gba\nSize = 4194304\nFolder = -\nAttributes = A -rw-r--r--\nEncrypted = -\n\n"
              "Path = Game\nSize = 0\nFolder = +\nAttributes = D drwxr-xr-x\nEncrypted = -\n\n"
              "Path = Game/link\nSize = 10\nFolder = -\nAttributes = A lrwxrwxrwx\nEncrypted = -\n\n"
              "Path = Secret.gba\nSize = 5\nFolder = -\nAttributes = A\nEncrypted = +\n")

    def test_parse(self):
        e = A.parse_7z_listing(self.SAMPLE)
        self.assertEqual([x["path"] for x in e], ["Game/Game.gba", "Game", "Game/link", "Secret.gba"])
        self.assertEqual(e[0]["size"], 4194304)
        self.assertTrue(e[1]["dir"])
        self.assertTrue(e[2]["link"])
        self.assertTrue(e[3]["encrypted"])

    def test_check_flags_links_and_passwords(self):
        listing = {"entries": A.parse_7z_listing(self.SAMPLE), "total": 4194319}
        probs = " ".join(A.check_listing(listing, 4000000, IM.DEFAULT_LIMITS))
        self.assertIn("symlink", probs)
        self.assertIn("mật khẩu", probs)


class Volumes(unittest.TestCase):
    def test_later_parts_are_hidden(self):
        for n in ("Game.part2.rar", "Game.part10.rar", "Game.r00", "Game.7z.002"):
            self.assertTrue(A.is_volume_rest(n), n)
        for n in ("Game.part1.rar", "Game.rar", "Game.7z", "Game.7z.001", "Part2.gba"):
            self.assertFalse(A.is_volume_rest(n), n)


class ImporterIntegration(Base):
    def test_archive_in_import_shows_needs_unpack_and_parts_are_hidden(self):
        with tarfile.open(self.imp / "Pack.tar.gz", "w:gz") as t:
            add_bytes(t, "a.gba", b"x")
        (self.imp / "Big.part2.rar").write_bytes(b"x")
        sc = IM.scan(None, {"primary": None, "secondary": self.card})
        names = {u["name"]: u for u in sc["units"]}
        self.assertEqual(names["Pack.tar.gz"]["status"], "needs_unpack")
        self.assertNotIn("Big.part2.rar", names)

    def test_encrypted_zip_without_7zip_stays_blocked(self):
        import zipfile
        with zipfile.ZipFile(self.imp / "Z.zip", "w") as z:
            z.writestr("a.gba", b"x")
        b = bytearray((self.imp / "Z.zip").read_bytes())       # bật cờ "có mật khẩu" ở local header và central directory
        b[6] |= 1
        b[b.index(b"PK\x01\x02") + 8] |= 1
        (self.imp / "Z.zip").write_bytes(bytes(b))
        with mock.patch.object(A, "seven_zip", return_value=None):
            sc = IM.scan(None, {"primary": None, "secondary": self.card})
        u = [u for u in sc["units"] if u["name"] == "Z.zip"][0]
        self.assertEqual(u["status"], "blocked")
        with mock.patch.object(A, "seven_zip", return_value="/bin/7zz"):
            sc = IM.scan(None, {"primary": None, "secondary": self.card})
        self.assertEqual([u["status"] for u in sc["units"] if u["name"] == "Z.zip"], ["needs_unpack"])

    def test_7z_without_7zip_is_unsupported_with_reason(self):
        (self.imp / "Game.7z").write_bytes(b"7z\xbc\xaf\x27\x1c")
        with mock.patch.object(A, "seven_zip", return_value=None):
            sc = IM.scan(None, {"primary": None, "secondary": self.card})
        u = [u for u in sc["units"] if u["name"] == "Game.7z"][0]
        self.assertEqual(u["status"], "unsupported")
        self.assertIn("7-Zip", " ".join(u["reasons"]))


class Cli(Base):
    def run_cli(self, *argv):
        import engine.cli as CLI
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = CLI.main(["--primary", str(self.tmp / "sd1"), "--secondary", str(self.card),
                             "--userdata", str(self.tmp / "ud"), "--json", *argv])
        return code, json.loads(out.getvalue())

    def test_unpack_via_cli(self):
        (self.tmp / "sd1").mkdir()
        (self.tmp / "ud").mkdir()
        p = self.imp / "Pack.tar"
        with tarfile.open(p, "w") as t:
            add_bytes(t, "a.gba", b"x")
        code, r = self.run_cli("import", "unpack", str(p), "--yes")
        self.assertEqual(code, 0, r)
        self.assertTrue(r["success"])

    def test_refuses_archive_outside_import(self):
        (self.tmp / "sd1").mkdir()
        (self.tmp / "ud").mkdir()
        p = self.tmp / "Pack.tar"
        with tarfile.open(p, "w") as t:
            add_bytes(t, "a.gba", b"x")
        code, r = self.run_cli("import", "unpack", str(p), "--yes")
        self.assertNotEqual(code, 0)


@unittest.skipUnless(A.seven_zip(), "cần 7zz/7zzs (chạy trong container ARM64 hoặc trên máy)")
class RealSevenZip(Base):
    def test_7z_round_trip(self):
        src = self.tmp / "src" / "Game"
        src.mkdir(parents=True)
        (src / "Game.gba").write_bytes(os.urandom(4096))
        subprocess.run([A.seven_zip(), "a", "-t7z", "-bso0", "-bsp0", str(self.imp / "Game.7z"), str(src)], check=True)
        r = A.unpack(self.imp / "Game.7z")
        self.assertTrue(r["success"], r)
        self.assertTrue((self.imp / "Game" / "Game" / "Game.gba").is_file())

    def make(self, name, pw, header=False):
        src = self.tmp / "s.gba"
        src.write_bytes(os.urandom(2048))
        cmd = [A.seven_zip(), "a", "-t7z", "-p" + pw, "-bso0", "-bsp0"] + (["-mhe=on"] if header else [])
        subprocess.run(cmd + [str(self.imp / name), str(src)], check=True)
        return self.imp / name

    def test_password_archive_without_hint_asks(self):
        p = self.make("P.7z", "abc", header=True)
        r = A.unpack(p, auto_password=True)
        self.assertFalse(r["success"])
        self.assertTrue(r["needs_password"])
        self.assertEqual(r["candidates"], [])
        self.assertTrue(p.exists())
        self.assertEqual(self.leftovers(), [])

    def test_auto_password_from_side_txt_header_encrypted(self):
        p = self.make("Game.7z", "S3cret!", header=True)
        (self.imp / "readme.txt").write_text("Chúc vui\nPassword: wrong1\nMật khẩu: S3cret!\n", encoding="utf-8")
        remember = self.tmp / "ud" / A.REMEMBERED
        r = A.unpack(p, auto_password=True, remember_file=remember)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["password_source"], "readme.txt")
        self.assertTrue((self.imp / "Game" / "s.gba").is_file())
        self.assertEqual(remember.read_text(encoding="utf-8").splitlines(), ["S3cret!"])

    def test_auto_password_from_list_data_encrypted(self):
        p = self.make("Game.7z", "site.com")
        (self.imp / A.PASSWORD_LIST).write_text("# mỗi dòng một\nnope\nsite.com\n", encoding="utf-8")
        r = A.unpack(p, auto_password=True)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["password_source"], A.PASSWORD_LIST)

    def test_explicit_password_wrong_then_right(self):
        p = self.make("Game.7z", "abc")
        r = A.unpack(p, password="xyz")
        self.assertFalse(r["success"])
        self.assertTrue(r["wrong_password"])
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.imp / "Game").exists())
        r = A.unpack(p, password="abc")
        self.assertTrue(r["success"], r)
        self.assertEqual(r["password_source"], "bạn nhập")

    def test_encrypted_zip_goes_through_unpack(self):
        src = self.tmp / "s.gba"
        src.write_bytes(os.urandom(1024))
        subprocess.run([A.seven_zip(), "a", "-tzip", "-pzz", "-bso0", "-bsp0", str(self.imp / "Z.zip"), str(src)], check=True)
        sc = IM.scan(None, {"primary": None, "secondary": self.card})
        self.assertEqual([u["status"] for u in sc["units"] if u["name"] == "Z.zip"], ["needs_unpack"])
        (self.imp / "Z [pass zz].txt").write_text("", encoding="utf-8")
        (self.imp / "pass.txt").write_text("zz\n", encoding="utf-8")
        r = A.unpack(self.imp / "Z.zip", auto_password=True)
        self.assertTrue(r["success"], r)
        self.assertTrue((self.imp / "Z" / "s.gba").is_file())

    def test_without_auto_only_reports(self):
        p = self.make("Game.7z", "abc")
        (self.imp / "pass.txt").write_text("abc\n", encoding="utf-8")
        r = A.unpack(p)
        self.assertTrue(r["needs_password"])
        self.assertEqual(r["tried"], 0)
        self.assertEqual([c["password"] for c in r["candidates"]], ["abc"])


class PasswordCandidates(Base):
    def cands(self, name="Game.rar", comment="", remember=None):
        p = self.imp / name
        p.write_bytes(b"x")
        return [(c["password"], c["source"]) for c in A.password_candidates(p, remember, comment=comment)]

    def test_comment_line_and_single_word(self):
        self.assertEqual(self.cands(comment="Welcome!\nPass: retro123\n"), [("retro123", "chú thích archive")])
        self.assertEqual(self.cands(comment="www.example.org"), [("www.example.org", "chú thích archive")])
        self.assertEqual(self.cands(comment="chỉ là một câu dài"), [])

    def test_file_name_hint(self):
        self.assertEqual(self.cands(name="Game [pass=abc].7z"), [("abc", "tên file")])
        self.assertEqual(self.cands(name="Game (pw xyz).rar"), [("xyz", "tên file")])

    def test_side_files_list_and_remembered_dedup_in_order(self):
        (self.imp / "info.nfo").write_text("pass = \"quoted\"\nrandom line\n", encoding="utf-8")
        (self.imp / "password.txt").write_text("line1\nquoted\n", encoding="utf-8")
        (self.imp / "notes.txt").write_text("không có gì\n", encoding="utf-8")
        (self.imp / A.PASSWORD_LIST).write_text("mine\n#comment\n\n", encoding="utf-8")
        rem = self.tmp / "rem.txt"
        rem.write_text("old\nmine\n", encoding="utf-8")
        got = self.cands(comment="mk: c1", remember=rem)
        self.assertEqual([x[0] for x in got], ["c1", "quoted", "line1", "mine", "old"])
        self.assertEqual(dict(got)["line1"], "password.txt")

    def test_capped(self):
        (self.imp / A.PASSWORD_LIST).write_text("\n".join(f"p{i}" for i in range(100)), encoding="utf-8")
        self.assertEqual(len(self.cands()), A.MAX_CANDIDATES)


if __name__ == "__main__":
    unittest.main()
