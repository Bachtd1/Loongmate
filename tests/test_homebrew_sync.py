"""pc/homebrew_sync.py: hanh vi that voi HTTP gia (khong mang), thu muc tam.

Chung minh test biet do: dat HOMEBREW_SYNC_PATH toi mot ban homebrew_sync.py co tinh lam hong
(vd. license_verdict luon 'ok', hoac hash_mismatch luon None) -> cac test tuong ung phai do.
"""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_override = os.environ.get("HOMEBREW_SYNC_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("homebrew_sync_under_test", _override)
    HS = importlib.util.module_from_spec(_spec)
    sys.modules["homebrew_sync_under_test"] = HS
    _spec.loader.exec_module(HS)
else:
    sys.path.insert(0, str(ROOT / "pc"))
    import homebrew_sync as HS

GB_BYTES = b"GBROM-" * 100
GBA_BYTES = b"GBAROM-" * 100


def entry(slug, title=None, license="MIT", filename=None, repo="https://github.com/gbdev/database", **extra):
    e = {"slug": slug, "title": title or slug, "developer": "Dev", "baserepo": repo,
         "files": [{"default": True, "filename": filename or slug + ".gb", "playable": True}]}
    if license is not None:
        e["license"] = license
    e.update(extra)
    return e


class FakeNet:
    """fetch(url) gia: search theo platform/page + file theo URL. Ghi lai moi yeu cau."""

    def __init__(self, entries_by_platform, files, page_size=2):
        self.entries, self.files, self.page_size = entries_by_platform, files, page_size
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        u = urllib.parse.urlparse(url)
        if u.path == "/api/search":
            q = urllib.parse.parse_qs(u.query)
            plat, page = q["platform"][0], int(q["page"][0])
            items = self.entries.get(plat, [])
            chunk = items[(page - 1) * self.page_size: page * self.page_size]
            total = max(1, -(-len(items) // self.page_size))
            return json.dumps({"results": len(items), "page_total": total, "page_current": page, "entries": chunk}).encode()
        if url in self.files:
            v = self.files[url]
            if isinstance(v, Exception):
                raise v
            return v
        raise HS.FetchError("HTTP 404", 404)

    def file_calls(self):
        return [c for c in self.calls if "/api/search" not in c]


def url_of(e, fn=None):
    return HS.raw_url(e, fn or e["files"][0]["filename"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "lib"
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.log = []

    def sync(self, net, **kw):
        s = HS.Syncer(self.dest, net, out=self.log.append, **kw)
        code = s.run()
        return s, code

    def catalog(self):
        return json.loads((self.dest / HS.CATALOG_NAME).read_text(encoding="utf-8"))["entries"]


class LicenseTests(unittest.TestCase):
    def verdict(self, **kw):
        return HS.license_verdict({"slug": "x", **kw})

    def test_whitelist_accepted(self):
        for lic in ("MIT", "Zlib", "ZLib", "GPL-3.0", "GPL-3.0-or-later", "GPL-3.0-only", "GPL-2.0-or-later", "LGPL-2.1",
                    "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "Unlicense", "0BSD", "CC0-1.0",
                    "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-SA 4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
                    "CC-BY-NC-ND-4.0", "CC-BY-ND-4.0", "public domain"):
            self.assertEqual(self.verdict(license=lic)[0], "ok", lic)

    def test_missing_or_empty_rejected(self):
        self.assertEqual(self.verdict()[0], "no_license")
        self.assertEqual(self.verdict(license="")[0], "no_license")
        self.assertEqual(self.verdict(license="   ")[0], "no_license")

    def test_unrecognized_rejected(self):
        for lic in ("Beta Cat", "CC-BY ish", "MIT / CC-BY-4.0 (Assets)", "WTFPL-2.0", "FSFAP", "CC-BY-NC-SA",
                    "All rights reserved", "GPL", "MIT OR WTFPL", 3, ["MIT"]):
            r = self.verdict(license=lic)
            self.assertEqual(r[0], "unrecognized_license", lic)

    def test_game_license_wins_and_assets_must_also_pass(self):
        self.assertEqual(self.verdict(license="Beta Cat", gameLicense="MIT")[0], "ok")
        self.assertEqual(self.verdict(gameLicense="MIT", assetsLicense="CC-BY-NC-4.0")[0], "ok")
        self.assertEqual(self.verdict(gameLicense="MIT", assetsLicense="proprietary")[0], "unrecognized_license")
        self.assertEqual(self.verdict(assetsLicense="MIT")[0], "no_license")  # chi tai nguyen, game khong co


class HelperTests(unittest.TestCase):
    def test_fat32_names(self):
        f = HS.fat32_name
        self.assertEqual(f('a<b>c:d"e/f\\g|h?i*j'), "a_b_c -d_e_f_g_h_i_j")
        self.assertEqual(f("Game. "), "Game")
        self.assertEqual(f("...hidden"), "hidden")
        self.assertEqual(f("CON"), "_CON")
        self.assertEqual(f("nul.test"), "_nul.test")
        self.assertEqual(f(""), "game")
        self.assertEqual(f("a\x00b\x1fc"), "a_b_c")
        self.assertEqual(f("x" * 300), "x" * 100)
        self.assertEqual(f("  two   spaces  "), "two spaces")
        self.assertEqual(f("Pokémon"), "Pokémon")  # NFC

    def test_raw_url_rules(self):
        e = {"slug": "2048gb", "baserepo": "https://github.com/gbdev/database"}
        self.assertEqual(HS.raw_url(e, "2048.gb"),
                         "https://raw.githubusercontent.com/gbdev/database/master/entries/2048gb/2048.gb")
        self.assertIn("/files/My%20Game.gba", HS.raw_url(e, "files/My Game.gba"))
        self.assertIsNone(HS.raw_url(e, "../x.gb"))
        self.assertIsNone(HS.raw_url({"slug": "../x", "baserepo": e["baserepo"]}, "a.gb"))
        self.assertIsNone(HS.raw_url({"slug": "x", "baserepo": "https://evil.example/a/b"}, "a.gb"))

    def test_pick_rom_and_ext(self):
        e = {"files": [{"filename": "a.zip", "default": True}, {"filename": "b.gb", "playable": True},
                       {"filename": "c.gbc", "default": True, "playable": True}]}
        self.assertEqual(HS.pick_rom(e)["filename"], "c.gbc")
        self.assertIsNone(HS.pick_rom({"files": [{"filename": "a.zip", "default": True}]}))

    def test_hash_mismatch(self):
        d = b"abc"
        good = {"md5": hashlib.md5(d).hexdigest(), "sha1": hashlib.sha1(d).hexdigest(),
                "sha256": hashlib.sha256(d).hexdigest().upper()}
        self.assertIsNone(HS.hash_mismatch(d, good))
        self.assertIsNotNone(HS.hash_mismatch(d, {**good, "sha1": "0" * 40}))
        self.assertIsNotNone(HS.hash_mismatch(d, hashlib.sha256(b"x").hexdigest()))  # chuoi tran, doan theo do dai
        self.assertIsNone(HS.hash_mismatch(d, None))

    def test_libretro_dat_parse(self):
        dat = 'clrmamepro (\n\tname "X"\n)\ngame (\n\tname "Real Name"\n\tdescription "d"\n' \
              '\trom ( name "a.gb" size 1 crc AA md5 BB sha1 %s )\n)\n' % ("AB" * 20)
        self.assertEqual(HS.parse_libretro_dat(dat), {("ab" * 20): "Real Name"})

    def test_throttle_spacing(self):
        t = [100.0]
        sleeps = []
        f = HS.HttpFetcher(min_interval=1.0, clock=lambda: t[0], sleep=lambda s: (sleeps.append(s), t.__setitem__(0, t[0] + s)))
        f._throttle()
        t[0] += 0.3
        f._throttle()
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], 0.7)
        t[0] += 5
        f._throttle()
        self.assertEqual(len(sleeps), 1)


class SyncTests(Base):
    def make(self):
        a = entry("alpha", "Alpha: The Game", "MIT")
        nc = entry("nc-game", "NC Game", "CC-BY-NC-ND-4.0")
        nolic = entry("nolic", "No License", None)
        weird = entry("weird", "Weird", "Beta Cat")
        empty = entry("empty", "Empty", "")
        nodl = entry("nodl", "No Download", "MIT", **{"use-requirements": {"disable-downloads": True}})
        zipo = entry("zipo", "Zip Only", "MIT", filename="zipo.zip")
        gbc = entry("colorful", "Colorful", "Zlib", filename="colorful.gbc")
        gba = entry("advance", "Advance", "GPL-3.0-or-later", filename="files/adv.gba",
                    repo="https://github.com/gbadev-org/games")
        files = {url_of(a): GB_BYTES, url_of(nc): GB_BYTES + b"nc", url_of(nolic): b"x", url_of(weird): b"x",
                 url_of(empty): b"x", url_of(nodl): b"x", url_of(zipo): b"x", url_of(gbc): b"GBC" * 50,
                 url_of(gba): GBA_BYTES}
        net = FakeNet({"GB": [a, nc, nolic, weird, empty, nodl], "GBC": [gbc, zipo], "GBA": [gba], "NES": []}, files)
        return net, dict(a=a, nc=nc, gbc=gbc, gba=gba)

    def test_filters_and_layout(self):
        net, e = self.make()
        s, code = self.sync(net)
        self.assertEqual(code, 0)
        self.assertEqual((self.dest / "GB" / "Alpha - The Game.gb").read_bytes(), GB_BYTES)
        self.assertEqual((self.dest / "GB" / "NC Game.gb").read_bytes(), GB_BYTES + b"nc")
        self.assertEqual((self.dest / "GBC" / "Colorful.gbc").read_bytes(), b"GBC" * 50)
        self.assertEqual((self.dest / "GBA" / "Advance.gba").read_bytes(), GBA_BYTES)
        lic = (self.dest / "GB" / "Alpha - The Game.license.txt").read_text(encoding="utf-8")
        self.assertIn("MIT", lic)
        self.assertIn("alpha", lic)
        self.assertIn(url_of(e["a"]), lic)
        # loai: khong tai, khong tao file
        for slug in ("nolic", "weird", "empty", "nodl", "zipo"):
            self.assertFalse(any(slug in c for c in net.file_calls()), slug)
        self.assertEqual(sorted(p.name for p in (self.dest / "GB").iterdir() if not p.name.endswith(".license.txt")),
                         ["Alpha - The Game.gb", "NC Game.gb"])
        gb = s.stats["GB"]["rejected"]
        self.assertEqual((gb["no_license"], gb["unrecognized_license"], gb["downloads_disabled"]), (2, 1, 1))
        self.assertEqual(s.stats["GBC"]["rejected"]["no_rom_file"], 1)
        cat = {r["slug"]: r for r in self.catalog()}
        self.assertEqual(set(cat), {"alpha", "nc-game", "colorful", "advance"})
        r = cat["alpha"]
        self.assertEqual((r["system"], r["file"], r["license"], r["author"], r["sha256"], r["source_url"], r["title"]),
                         ("GB", "GB/Alpha - The Game.gb", "MIT", "Dev", hashlib.sha256(GB_BYTES).hexdigest(), url_of(e["a"]),
                          "Alpha: The Game"))
        self.assertFalse(list(self.dest.rglob("*.partial")))

    def test_paginates_and_systems_filter(self):
        net, _ = self.make()
        self.sync(net, systems=("GBA",))
        self.assertTrue((self.dest / "GBA" / "Advance.gba").exists())
        self.assertFalse((self.dest / "GB").exists())
        self.assertFalse(any("platform=GB&" in c for c in net.calls))
        net2, _ = self.make()
        self.sync(net2, systems=("GB",), refresh=True)  # GB co 6 muc / 2 moi trang = 3 trang
        self.assertEqual(sum("platform=GB&" in c for c in net2.calls), 3)

    def test_wrong_catalog_hash_rejected(self):
        e = entry("hashed", "Hashed")
        e["files"][0]["hash"] = {"sha256": hashlib.sha256(b"other").hexdigest()}
        net = FakeNet({"GB": [e]}, {url_of(e): GB_BYTES})
        s, code = self.sync(net, systems=("GB",))
        self.assertEqual(s.stats["GB"]["rejected"]["hash_mismatch"], 1)
        self.assertEqual(code, 0)
        self.assertFalse((self.dest / "GB" / "Hashed.gb").exists())
        self.assertFalse(list(self.dest.rglob("*.partial")))
        self.assertFalse((self.dest / HS.CATALOG_NAME).exists())

    def test_right_catalog_hash_accepted(self):
        e = entry("hashed", "Hashed")
        e["files"][0]["hash"] = {"md5": hashlib.md5(GB_BYTES).hexdigest(), "sha256": hashlib.sha256(GB_BYTES).hexdigest()}
        net = FakeNet({"GB": [e]}, {url_of(e): GB_BYTES})
        self.sync(net, systems=("GB",))
        self.assertEqual((self.dest / "GB" / "Hashed.gb").read_bytes(), GB_BYTES)

    def test_foreign_file_not_overwritten(self):
        net, _ = self.make()
        (self.dest / "GB").mkdir(parents=True)
        (self.dest / "GB" / "Alpha - The Game.gb").write_bytes(b"MINE")
        s, _ = self.sync(net, systems=("GB",))
        self.assertEqual((self.dest / "GB" / "Alpha - The Game.gb").read_bytes(), b"MINE")
        self.assertTrue(sum(s.stats["GB"]["skipped"].values()) == 1)
        self.assertNotIn("alpha", {r["slug"] for r in self.catalog()})
        self.assertFalse(any(url_of(entry("alpha")) == c for c in net.file_calls()))

    def test_rerun_downloads_nothing_then_changed_hash_redownloads(self):
        net, e = self.make()
        self.sync(net)
        n = len(net.file_calls())
        self.assertGreater(n, 0)
        net.calls.clear()
        s, _ = self.sync(net)
        self.assertEqual(net.file_calls(), [])  # khong tai lai file nao
        self.assertEqual(sum(st["unchanged"] for st in s.stats.values()), 4)
        # catalog bao hash moi -> file doi -> tai lai dung 1 file
        new = GB_BYTES + b"v2"
        e["a"]["files"][0]["hash"] = {"sha256": hashlib.sha256(new).hexdigest()}
        net.files[url_of(e["a"])] = new
        net.calls.clear()
        self.sync(net, refresh=True)
        self.assertEqual(net.file_calls(), [url_of(e["a"])])
        self.assertEqual((self.dest / "GB" / "Alpha - The Game.gb").read_bytes(), new)
        self.assertEqual({r["slug"]: r for r in self.catalog()}["alpha"]["sha256"], hashlib.sha256(new).hexdigest())

    def test_user_modified_tool_file_not_overwritten(self):
        net, e = self.make()
        self.sync(net)
        p = self.dest / "GB" / "Alpha - The Game.gb"
        p.write_bytes(b"EDITED")
        new = GB_BYTES + b"v2"
        e["a"]["files"][0]["hash"] = {"sha256": hashlib.sha256(new).hexdigest()}
        net.files[url_of(e["a"])] = new
        self.sync(net, refresh=True)
        self.assertEqual(p.read_bytes(), b"EDITED")

    def test_atomic_write_failure_keeps_old_file(self):
        net, e = self.make()
        self.sync(net, systems=("GB",))
        p = self.dest / "GB" / "Alpha - The Game.gb"
        new = GB_BYTES + b"v2"
        e["a"]["files"][0]["hash"] = {"sha256": hashlib.sha256(new).hexdigest()}
        net.files[url_of(e["a"])] = new
        with mock.patch.object(HS.os, "replace", side_effect=OSError("disk full")):
            s, code = self.sync(net, systems=("GB",), refresh=True)
        self.assertEqual(code, 1)
        self.assertEqual(s.stats["GB"]["errors"], 1)
        self.assertEqual(p.read_bytes(), GB_BYTES)
        self.assertFalse(list(self.dest.rglob("*.partial")))

    def test_dry_run_downloads_and_writes_nothing(self):
        net, _ = self.make()
        s, code = self.sync(net, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(net.file_calls(), [])
        left = [p for p in self.dest.rglob("*") if ".homebrew-cache" not in p.parts and p.is_file()]
        self.assertEqual(left, [])
        self.assertEqual(sum(st["accepted"] for st in s.stats.values()), 4)
        self.assertEqual(sum(st["downloaded"] for st in s.stats.values()), 4)  # "se tai"

    def test_limit(self):
        net, _ = self.make()
        self.sync(net, limit=2)
        self.assertEqual(len(net.file_calls()), 2)
        self.assertEqual({r["system"] for r in self.catalog()}, {"GB", "GBC"})  # xen ke giua cac he

    def test_download_error_counted_others_continue(self):
        net, e = self.make()
        net.files[url_of(e["a"])] = HS.FetchError("HTTP 404", 404)
        s, code = self.sync(net)
        self.assertEqual(code, 1)
        self.assertEqual(s.stats["GB"]["errors"], 1)
        self.assertTrue((self.dest / "GB" / "NC Game.gb").exists())
        self.assertFalse((self.dest / "GB" / "Alpha - The Game.gb").exists())

    def test_same_title_different_slug_and_cgb_ext(self):
        a = entry("one", "Same", "MIT")
        b = entry("two", "Same", "MIT", filename="two.cgb")
        net = FakeNet({"GB": [a, b]}, {url_of(a): b"1", url_of(b): b"2"})
        self.sync(net, systems=("GB",))
        # 'two' co duoi .cgb -> he GBC (khong nam trong systems GB) nen khong tai; 'one' o GB
        self.assertEqual((self.dest / "GB" / "Same.gb").read_bytes(), b"1")
        c = entry("two", "Same", "MIT")
        net = FakeNet({"GB": [a, c]}, {url_of(a): b"1", url_of(c): b"2"})
        self.dest = self.tmp / "lib2"
        self.sync(net, systems=("GB",))
        names = sorted(p.name for p in (self.dest / "GB").iterdir() if p.suffix == ".gb")
        self.assertEqual(names, ["Same [two].gb", "Same.gb"])

    def test_cgb_saved_as_gbc(self):
        a = entry("cg", "CG", "MIT", filename="cg.cgb")
        net = FakeNet({"GBC": [a]}, {url_of(a): b"1"})
        self.sync(net, systems=("GBC",))
        self.assertTrue((self.dest / "GBC" / "CG.gbc").exists())

    def test_libretro_match_recorded(self):
        net, e = self.make()
        sha1 = hashlib.sha1(GB_BYTES).hexdigest()
        dat = 'game (\n\tname "Canonical Alpha"\n\trom ( name "a.gb" size 1 crc AA md5 BB sha1 %s )\n)\n' % sha1.upper()
        net.files[HS.DAT_BASE + urllib.parse.quote("Nintendo - Game Boy.dat")] = dat.encode()
        net.files[HS.DAT_BASE + urllib.parse.quote("Nintendo - Game Boy Advance.dat")] = b""
        s, _ = self.sync(net, libretro=True)
        self.assertEqual({r["slug"]: r for r in self.catalog()}["alpha"]["libretro_name"], "Canonical Alpha")
        self.assertEqual(s.stats["GB"]["libretro"], 1)

    def test_corrupt_catalog_aborts_without_touching_files(self):
        net, _ = self.make()
        self.dest.mkdir(parents=True)
        (self.dest / HS.CATALOG_NAME).write_text("{not json", encoding="utf-8")
        with self.assertRaises(HS.FetchError):
            self.sync(net)
        self.assertEqual(net.file_calls(), [])

    def test_cli_main_dry_run_and_bad_args(self):
        net, _ = self.make()
        out = []
        self.assertEqual(HS.main(["--dest", str(self.dest), "--dry-run"], fetch=net, out=out.append), 0)
        self.assertTrue(any("BAO CAO" in l for l in out))
        self.assertEqual(HS.main(["--dest", str(self.dest), "--systems", "snes"], fetch=net, out=out.append), 2)

    def test_real_systems_json_folders(self):
        s = HS.Syncer(self.dest, lambda u: b"")
        self.assertEqual(s.folder, {"GB": "GB", "GBC": "GBC", "GBA": "GBA", "NES": "NES"})


if __name__ == "__main__":
    unittest.main()
