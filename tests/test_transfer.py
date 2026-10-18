"""
Tests for engine/transfer.py (chuyển game giữa hai thẻ) and engine/artwork.py (ảnh bìa chéo thẻ),
plus dedupe's copy_artwork option.

Mọi test chạy hành vi thật trên bố cục thẻ tạm. Để chứng minh test biết đỏ, đặt
LOONGMATE_TRANSFER_PATH tới một bản engine/transfer.py cố tình làm hỏng; test sẽ chạy bản đó.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.artwork as AW
import engine.dedupe as DD
from engine.inventory import InventoryEngine

_override = os.environ.get("LOONGMATE_TRANSFER_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("transfer_under_test", _override)
    TR = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(TR)
else:
    import engine.transfer as TR

MB = 1024 * 1024
ROM = b"ROM-" + bytes(range(256)) * 8
ART = b"\x89PNG-art-" + bytes(range(64))
SAVE = b"SAVE-" + bytes(range(32))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_transfer_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir()
        self.free = {"sd1": 900 * MB, "sd2": 25 * 1024 * MB}
        p = mock.patch.object(TR, "_free_bytes", self._fake_free)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_free(self, root):
        return self.free["sd1" if str(Path(root).resolve()).startswith(str(self.sd1.resolve())) else "sd2"]

    def w(self, path, data=ROM):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, rom, dest="secondary", **kw):
        return TR.plan_move(rom, self.sd1, self.sd2, dest, **kw)

    def run_move(self, plan):
        return TR.execute_move(plan, self.ud, self.sd1, self.sd2)

    def ledger(self):
        f = self.ud / "transfer-ledger.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def tree(self, root):
        return sorted(str(p.relative_to(root)).replace("\\", "/") for p in Path(root).rglob("*") if p.is_file())


class TestMoveSingleRom(Base):
    def test_01_rom_art_and_saves_move_but_saves_stay_at_source(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        self.w(self.sd1 / "Saves/Game.srm", SAVE)
        self.w(self.sd1 / "States/Game.state1", SAVE + b"s")
        old = time.time() - 86400 * 30
        os.utime(rom, (old, old))
        pl = self.plan(rom)
        self.assertTrue(pl["ok"], pl["message"])
        self.assertTrue(any("CHƯA KIỂM CHỨNG" in w for w in pl["warnings"]))
        res = self.run_move(pl)
        self.assertFalse(res["rolled_back"])
        self.assertEqual(res["refused"], [])
        # đích đủ file, đúng nội dung
        self.assertEqual((self.sd2 / "Roms/GBA/Game.gba").read_bytes(), ROM)
        self.assertEqual((self.sd2 / "Images/GBA/Game.png").read_bytes(), ART)
        self.assertEqual((self.sd2 / "Saves/Game.srm").read_bytes(), SAVE)
        self.assertTrue((self.sd2 / "States/Game.state1").is_file())
        # nguồn: ROM + ảnh mất, save/state còn
        self.assertFalse(rom.exists())
        self.assertFalse((self.sd1 / "Images/GBA/Game.png").exists())
        self.assertEqual((self.sd1 / "Saves/Game.srm").read_bytes(), SAVE)
        self.assertTrue((self.sd1 / "States/Game.state1").is_file())
        self.assertEqual(res["bytes_moved"], len(ROM) + len(ART))
        # giữ mtime, không còn .partial
        self.assertAlmostEqual((self.sd2 / "Roms/GBA/Game.gba").stat().st_mtime, old, delta=2)
        self.assertEqual([f for f in self.tree(self.tmp) if f.endswith(".partial")], [])

    def test_02_ledger_has_intent_before_copied_verified_before_source_removed(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.run_move(self.plan(rom))
        st = [e["status"] for e in self.ledger()]
        self.assertEqual(st, ["intent", "copied", "verified", "source_remove_intent", "source_removed"])

    def test_03_include_saves_false_and_same_card_refused(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Saves/Game.srm", SAVE)
        pl = self.plan(rom, include_saves=False)
        self.assertEqual([e["kind"] for e in pl["files"]], ["rom"])
        bad = self.plan(rom, dest="primary")
        self.assertFalse(bad["ok"])
        self.assertIn("đã nằm ở thẻ đích", bad["message"])


class TestMoveMultiFile(Base):
    def test_04_cue_moves_with_all_tracks_and_leaves_neighbours(self):
        d = self.sd1 / "Roms/PSX/Game"
        cue = self.w(d / "Game.cue", b'FILE "Game (Track 1).bin" BINARY\nFILE "Game (Track 2).bin" BINARY\n')
        self.w(d / "Game (Track 1).bin", b"t1" * 100)
        self.w(d / "Game (Track 2).bin", b"t2" * 100)
        self.w(self.sd1 / "Roms/PSX/Other.chd", b"other")
        self.w(self.sd1 / "Images/PSX/Game.png", ART)
        pl = self.plan(cue)
        self.assertTrue(pl["ok"], pl["message"])
        self.assertEqual(sorted(e["kind"] for e in pl["files"]), ["artwork", "playlist", "track", "track"])
        res = self.run_move(pl)
        self.assertEqual(res["refused"], [])
        self.assertEqual(self.tree(self.sd2), ["Images/PSX/Game.png", "Roms/PSX/Game/Game (Track 1).bin",
                                               "Roms/PSX/Game/Game (Track 2).bin", "Roms/PSX/Game/Game.cue"])
        self.assertEqual(self.tree(self.sd1), ["Roms/PSX/Other.chd"])

    def test_05_m3u_moves_discs_and_their_cues(self):
        d = self.sd1 / "Roms/PSX"
        m3u = self.w(d / "Big.m3u", b"Big (Disc 1).cue\nBig (Disc 2).cue\n")
        for n in (1, 2):
            self.w(d / f"Big (Disc {n}).cue", f'FILE "Big (Disc {n}).bin" BINARY\n'.encode())
            self.w(d / f"Big (Disc {n}).bin", b"d" * 50)
        pl = self.plan(m3u)
        self.assertTrue(pl["ok"], pl["message"])
        self.assertEqual(len(pl["files"]), 5)
        self.assertEqual(self.run_move(pl)["refused"], [])
        self.assertEqual(len(self.tree(self.sd2)), 5)
        self.assertEqual(self.tree(self.sd1), [])

    def test_06_track_shared_with_other_playlist_is_refused(self):
        d = self.sd1 / "Roms/PSX"
        a = self.w(d / "A.cue", b'FILE "shared.bin" BINARY\n')
        self.w(d / "B.cue", b'FILE "shared.bin" BINARY\n')
        self.w(d / "shared.bin", b"x")
        pl = self.plan(a)
        self.assertFalse(pl["ok"])
        self.assertIn("tham chiếu", pl["message"])
        self.assertEqual(self.tree(self.sd2), [])


class TestAtomicity(Base):
    def _game(self):
        d = self.sd1 / "Roms/PSX/Game"
        cue = self.w(d / "Game.cue", b'FILE "a.bin" BINARY\nFILE "b.bin" BINARY\n')
        self.w(d / "a.bin", b"A" * 1000)
        self.w(d / "b.bin", b"B" * 1000)
        self.w(self.sd1 / "Images/PSX/Game.png", ART)
        return cue

    def test_07_hash_mismatch_midway_rolls_back_whole_game(self):
        cue = self._game()
        before = {f: (self.sd1 / f).read_bytes() for f in self.tree(self.sd1)}
        pl = self.plan(cue)
        real, calls = TR.copy_file_hashed, []

        def corrupting(src, dst):
            h = real(src, dst)
            calls.append(dst)
            if len(calls) == 2:  # file thứ hai bị hỏng sau khi chép
                with open(dst, "r+b") as f:
                    f.write(b"\xff")
            return h

        with mock.patch.object(TR, "copy_file_hashed", corrupting):
            res = self.run_move(pl)
        self.assertTrue(res["rolled_back"])
        self.assertEqual(res["moved"], [])
        self.assertIn("SHA-256", res["refused"][0]["reason"])
        self.assertEqual({f: (self.sd1 / f).read_bytes() for f in self.tree(self.sd1)}, before)
        self.assertEqual(self.tree(self.sd2), [])
        self.assertFalse((self.sd2 / "Roms").exists(), "thư mục tạo dở phải được dọn")
        self.assertEqual(self.ledger()[-1]["status"], "rolled_back")

    def test_08_io_error_midway_also_rolls_back(self):
        cue = self._game()
        pl = self.plan(cue)
        real, n = TR.copy_file_hashed, []

        def failing(src, dst):
            n.append(1)
            if len(n) == 3:
                raise OSError("thẻ đích lỗi ghi")
            return real(src, dst)

        with mock.patch.object(TR, "copy_file_hashed", failing):
            res = self.run_move(pl)
        self.assertTrue(res["rolled_back"])
        self.assertEqual(len(self.tree(self.sd1)), 4)
        self.assertEqual(self.tree(self.sd2), [])

    def test_09_source_is_never_removed_before_destination_is_verified(self):
        """Mỗi lần xoá nguồn xảy ra, mọi bản đích của game phải đã tồn tại đúng nội dung."""
        cue = self._game()
        pl = self.plan(cue)
        want = {e["dst"]: sha(Path(e["src"]).read_bytes()) for e in pl["files"]}
        real_remove, checked = os.remove, []

        def spying(p, *a, **k):
            if str(p).startswith(str(self.sd1)):
                for dst, h in want.items():
                    ok = Path(dst).is_file() and sha(Path(dst).read_bytes()) == h
                    checked.append(ok)
                    self.assertTrue(ok, f"nguồn bị xoá khi đích {dst} chưa đủ/đúng")
            return real_remove(p, *a, **k)

        with mock.patch.object(TR.os, "remove", spying):
            res = self.run_move(pl)
        self.assertEqual(res["refused"], [])
        self.assertTrue(checked and all(checked))

    def test_10_source_changed_during_copy_rolls_back(self):
        cue = self._game()
        pl = self.plan(cue)
        real = TR.copy_file_hashed

        def touching(src, dst):
            h = real(src, dst)
            if Path(src).name == "a.bin":
                Path(src).write_bytes(b"changed!" * 10)
            return h

        with mock.patch.object(TR, "copy_file_hashed", touching):
            res = self.run_move(pl)
        self.assertTrue(res["rolled_back"])
        self.assertEqual(self.tree(self.sd2), [])


class TestRefusals(Base):
    def test_11_low_space_at_destination_is_refused_in_plan_and_execute(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        good = self.plan(rom)
        self.assertTrue(good["ok"])
        self.free["sd2"] = 150 * MB
        bad = self.plan(rom)
        self.assertFalse(bad["ok"])
        self.assertIn("200", bad["message"])
        res = self.run_move(good)  # kế hoạch cũ, thẻ đích vừa đầy
        self.assertEqual(self.tree(self.sd2), [])
        self.assertTrue(res["refused"])
        self.assertTrue(rom.exists())

    def test_12_dest_same_name_different_hash_is_refused_and_untouched(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        other = self.w(self.sd2 / "Roms/GBA/Game.gba", b"DIFFERENT-SAME-LENGTH".ljust(len(ROM), b"."))
        res = self.run_move(self.plan(rom))
        self.assertTrue(res["refused"])
        self.assertIn("khác nội dung", res["refused"][0]["reason"])
        self.assertEqual(other.read_bytes()[:9], b"DIFFERENT")
        self.assertEqual(rom.read_bytes(), ROM)
        self.assertFalse((self.sd2 / "Images").exists(), "không được chép dở file nào khi game bị từ chối")

    def test_13_dest_same_name_same_hash_counts_as_present_then_source_removed(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        res = self.run_move(self.plan(rom))
        self.assertEqual(res["refused"], [])
        self.assertFalse(rom.exists())
        self.assertEqual((self.sd2 / "Roms/GBA/Game.gba").read_bytes(), ROM)

    def test_14_paths_outside_allowed_zones_are_refused(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        bios = self.w(self.sd1 / "BIOS/gba_bios.bin")
        outside = self.w(self.tmp / "elsewhere/x.gba")
        good = self.plan(rom)
        for src, dst, kind in [
            (bios, self.sd2 / "BIOS/gba_bios.bin", "rom"),                 # ngoài Roms/
            (outside, self.sd2 / "Roms/GBA/x.gba", "rom"),                 # ngoài thẻ
            (rom, self.tmp / "elsewhere/Game.gba", "rom"),                 # đích ngoài thẻ
            (rom, self.sd2 / "Roms/GBA/../../BIOS/Game.gba", "rom"),       # đích traversal
            (rom, self.sd2 / "Roms/GBA/Game.gba", "bogus"),                # loại lạ
        ]:
            plan = dict(good, files=[{"src": str(src), "dst": str(dst), "size": 1, "kind": kind, "remove_src": True}])
            res = self.run_move(plan)
            self.assertTrue(res["refused"], (src, dst, kind))
            self.assertTrue(bios.exists() and outside.exists() and rom.exists())
        self.assertEqual(self.tree(self.sd2), [])
        self.assertFalse((self.tmp / "elsewhere/Game.gba").exists())
        # plan_move cũng từ chối đường dẫn ngoài Roms/
        self.assertFalse(self.plan(bios)["ok"])
        self.assertFalse(self.plan(outside)["ok"])

    def test_15_symlink_is_refused(self):
        real = self.w(self.tmp / "elsewhere/real.gba")
        link = self.sd1 / "Roms/GBA/Link.gba"
        link.parent.mkdir(parents=True)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            self.skipTest("không tạo được symlink trên máy này")
        self.assertFalse(self.plan(link)["ok"])
        forged = dict(self.plan(self.w(self.sd1 / "Roms/GBA/Game.gba")),
                      files=[{"src": str(link), "dst": str(self.sd2 / "Roms/GBA/Link.gba"),
                              "size": 1, "kind": "rom", "remove_src": True}])
        res = self.run_move(forged)
        self.assertTrue(res["refused"])
        self.assertTrue(real.exists())
        self.assertEqual(self.tree(self.sd2), [])

    def test_16_unwritable_ledger_blocks_everything(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        pl = self.plan(rom)
        with mock.patch.object(TR, "ledger_append", side_effect=OSError("đầy")):
            res = self.run_move(pl)
        self.assertTrue(res["refused"])
        self.assertTrue(rom.exists())
        self.assertEqual(self.tree(self.sd2), [])


class TestBulk(Base):
    def _lib(self):
        self.w(self.sd1 / "Roms/GBA/Big.gba", b"B" * (6 * MB))
        self.w(self.sd1 / "Roms/GBA/Mid.gba", b"M" * (3 * MB))
        self.w(self.sd1 / "Roms/GBA/Small.gba", b"S" * (1 * MB))
        self.w(self.sd1 / "Roms/GBA/Playing.gba", b"P" * (9 * MB))
        self.w(self.sd1 / "Saves/Playing.srm", SAVE)  # vừa ghi => đang chơi
        old = time.time() - 86400 * 10
        for n in ("Big", "Mid", "Small"):
            self.w(self.sd1 / f"Roms/GBA/{n}.gba", (self.sd1 / f"Roms/GBA/{n}.gba").read_bytes())
            os.utime(self.sd1 / f"Roms/GBA/{n}.gba", (old, old))
        return InventoryEngine(self.sd1, self.sd2).scan_all()

    def test_17_bulk_reaches_target_picks_smallest_sufficient_and_skips_playing(self):
        inv = self._lib()
        target = 900 + 4  # cần giải phóng 4 MB
        rep = TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=target)
        self.assertTrue(rep["ok"] and rep["reached"], rep["message"])
        names = [m["name"] for m in rep["moves"]]
        self.assertEqual(names, ["Big.gba"])           # nhỏ nhất đủ lớn (không lấy Playing 9 MB)
        self.assertNotIn("Playing.gba", names)
        self.assertTrue(any("đang chơi" in w for w in rep["warnings"]))
        self.assertGreaterEqual(rep["bytes_freed"], 4 * MB)

        rep2 = TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=900 + 8)
        self.assertTrue(rep2["reached"])
        self.assertEqual({m["name"] for m in rep2["moves"]}, {"Big.gba", "Mid.gba"})
        self.assertGreaterEqual(rep2["bytes_freed"], 8 * MB)

        # kế hoạch con thực thi được
        res = self.run_move(rep["moves"][0]["plan"])
        self.assertEqual(res["refused"], [])
        self.assertTrue((self.sd2 / "Roms/GBA/Big.gba").is_file())

    def test_18_bulk_unreachable_target_reports_honestly_and_target_met_needs_nothing(self):
        inv = self._lib()
        rep = TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=900 + 500)
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["reached"])
        self.assertIn("CHƯA đạt", rep["message"])
        self.assertEqual(TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=100)["moves"], [])
        self.assertFalse(TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary")["ok"])

    def test_19_bulk_respects_destination_space(self):
        inv = self._lib()
        self.free["sd2"] = 205 * MB  # chỉ chép thêm được ~5 MB
        rep = TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=900 + 8)
        self.assertFalse(rep["reached"])
        self.assertTrue(rep["skipped"] or rep["moves"])
        self.assertLessEqual(rep["bytes_copy"], 5 * MB)

    def test_20_bulk_system_filter(self):
        inv = self._lib()
        self.w(self.sd1 / "Roms/SNES/Zelda.sfc", b"Z" * (2 * MB))
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        rep = TR.plan_bulk_move(inv, self.sd1, self.sd2, "secondary", target_free_mb=900 + 1, system="SFC")
        self.assertEqual([m["name"] for m in rep["moves"]], ["Zelda.sfc"])


class TestArtwork(Base):
    def test_21_find_art_across_cards_and_aliases(self):
        rom = self.w(self.sd2 / "Roms/GENESIS/Sonic.md")
        self.assertIsNone(AW.find_art(rom, self.sd1, self.sd2))
        art = self.w(self.sd1 / "Images/MD/sonic.PNG", ART)  # alias MD, khác hoa thường, thẻ kia
        self.assertEqual(AW.find_art(rom, self.sd1, self.sd2), art)
        own = self.w(self.sd2 / "Images/GENESIS/Sonic.png", ART + b"2")
        self.assertEqual(AW.find_art(rom, self.sd1, self.sd2), own)  # ưu tiên thẻ chứa ROM
        self.assertIsNone(AW.find_art(self.tmp / "x.md", self.sd1, self.sd2))

    def test_22_ensure_art_copies_verified_and_is_idempotent(self):
        rom = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        r = AW.ensure_art_on_card(rom, self.sd2, self.sd1, self.sd2, self.ud)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "copied")
        self.assertEqual((self.sd2 / "Images/GBA/Game.png").read_bytes(), ART)
        self.assertTrue((self.sd1 / "Images/GBA/Game.png").exists())
        self.assertEqual([e["status"] for e in self.ledger()], ["intent", "copied", "verified"])
        self.assertEqual(AW.ensure_art_on_card(rom, self.sd2, self.sd1, self.sd2, self.ud)["status"], "already_present")

    def test_23_ensure_art_never_overwrites_different_art(self):
        rom = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        mine = self.w(self.sd2 / "Images/GBA/Game.png", b"MY-OWN-ART")
        r = AW.ensure_art_on_card(rom, self.sd2, self.sd1, self.sd2, self.ud)
        self.assertEqual(r["status"], "already_present")
        self.assertEqual(mine.read_bytes(), b"MY-OWN-ART")

    def test_24_ensure_art_no_art_and_wrong_card(self):
        rom = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.assertEqual(AW.ensure_art_on_card(rom, self.sd2, self.sd1, self.sd2, self.ud)["status"], "no_art")
        r = AW.ensure_art_on_card(rom, self.sd1, self.sd1, self.sd2, self.ud)  # ROM không thuộc card_root
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "refused")

    def test_25_ensure_art_hash_mismatch_removes_bad_copy(self):
        rom = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        real = AW.copy_file_hashed

        def corrupting(src, dst):
            h = real(src, dst)
            Path(dst).write_bytes(b"bad")
            return h

        with mock.patch.object(AW, "copy_file_hashed", corrupting):
            r = AW.ensure_art_on_card(rom, self.sd2, self.sd1, self.sd2, self.ud)
        self.assertFalse(r["ok"])
        self.assertFalse((self.sd2 / "Images/GBA/Game.png").exists())
        self.assertEqual((self.sd1 / "Images/GBA/Game.png").read_bytes(), ART)

    def test_26_move_art_lands_in_same_images_subfolder(self):
        rom = self.w(self.sd1 / "Roms/GENESIS/Sonic.md")
        self.w(self.sd1 / "Images/MD/Sonic.png", ART)
        self.assertEqual(self.run_move(self.plan(rom))["refused"], [])
        self.assertEqual((self.sd2 / "Images/MD/Sonic.png").read_bytes(), ART)


class TestDedupeCopyArtwork(Base):
    def _group(self):
        # người dùng chọn giữ bản SD2, nơi không có ảnh; ảnh nằm ở SD1 cùng bản bị xoá
        self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", ART)
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        from engine.doctor import LibraryDoctor
        cat = LibraryDoctor(self.sd1, self.sd2, ROOT / "systems.json", self.ud).systems_catalog
        fake = lambda root: mock.Mock(free=(900 * MB if str(root).startswith(str(self.sd1)) else 25 * 1024 * MB))
        with mock.patch.object(DD.shutil, "disk_usage", fake):
            return DD.find_duplicates(inv, cat, self.sd1, self.sd2, prefer_card="secondary")["groups"][0]

    def _run(self, g, **kw):
        return DD.execute_deletion([r["path"] for r in g["remove"]], self.ud, "duplicate",
                                   kept_path=g["keep"]["path"], expected_sha256=g["sha256"],
                                   primary_path=self.sd1, secondary_path=self.sd2, **kw)

    def test_27_copy_artwork_copies_art_before_deleting_duplicate(self):
        g = self._group()
        self.assertEqual(Path(g["keep"]["path"]), self.sd2 / "Roms/GBA/Game.gba")
        real_remove, seen = os.remove, []

        def spying(p, *a, **k):
            if Path(p).suffix == ".gba":
                seen.append((self.sd2 / "Images/GBA/Game.png").is_file())
            return real_remove(p, *a, **k)

        with mock.patch.object(DD.os, "remove", spying):
            res = self._run(g, copy_artwork=True)
        self.assertEqual(res["refused"], [])
        self.assertEqual(seen, [True], "ảnh phải có ở thẻ giữ TRƯỚC khi xoá bản trùng")
        self.assertEqual(res["artwork"]["status"], "copied")
        self.assertEqual((self.sd2 / "Images/GBA/Game.png").read_bytes(), ART)
        self.assertFalse((self.sd1 / "Roms/GBA/Game.gba").exists())

    def test_28_default_does_not_touch_artwork(self):
        g = self._group()
        res = self._run(g)
        self.assertNotIn("artwork", res)
        self.assertFalse((self.sd2 / "Images").exists())
        self.assertFalse((self.sd1 / "Roms/GBA/Game.gba").exists())


if __name__ == "__main__":
    unittest.main()
