"""
Tests for engine/dedupe.py (duplicate detection, safe deletion, quarantine purge).

All tests run real behaviour against temporary card layouts. To prove the tests
can go red, set LOONGMATE_DEDUPE_PATH to a deliberately broken copy of
engine/dedupe.py; the tests then exercise that copy instead of the real module.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.doctor import LibraryDoctor
from engine.inventory import InventoryEngine

_override = os.environ.get("LOONGMATE_DEDUPE_PATH")
if _override:
    _spec = importlib.util.spec_from_file_location("dedupe_under_test", _override)
    DD = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(DD)
else:
    import engine.dedupe as DD

CONTENT = b"GBA-ROM-" + bytes(range(256)) * 4


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class DedupeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_dedupe_"))
        self.sd1 = self.tmp / "sd1"
        self.sd2 = self.tmp / "sd2"
        self.ud = self.tmp / "ud" / "LoongMate"
        for d in (self.sd1, self.sd2, self.ud):
            d.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def w(self, path: Path, data: bytes = CONTENT) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def find(self, **kw):
        inv = InventoryEngine(self.sd1, self.sd2).scan_all()
        catalog = LibraryDoctor(self.sd1, self.sd2, ROOT / "systems.json", self.ud).systems_catalog
        # Mirror the real device: SD1 nearly full, SD2 roomy (temp dirs share one disk otherwise).
        def fake_usage(root):
            free = 900 * 1024 * 1024 if str(root).startswith(str(self.sd1)) else 25 * 1024 ** 3
            return SimpleNamespace(total=0, used=0, free=free)

        with mock.patch.object(DD.shutil, "disk_usage", fake_usage):
            return DD.find_duplicates(inv, catalog, self.sd1, self.sd2, **kw)

    def run_group(self, g):
        return DD.execute_deletion(
            [r["path"] for r in g["remove"]], self.ud, "duplicate", kept_path=g["keep"]["path"],
            expected_sha256=g["sha256"], primary_path=self.sd1, secondary_path=self.sd2)


class TestFindDuplicates(DedupeBase):
    def test_01_cross_card_group_keeps_card_with_more_free_space(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba")
        b = self.w(self.sd2 / "Roms/GBA/Game.gba")

        rep = self.find()
        self.assertEqual(len(rep["groups"]), 1)
        g = rep["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), b)
        self.assertEqual([Path(r["path"]) for r in g["remove"]], [a])
        self.assertIn("thẻ", g["keep_reason"])
        self.assertEqual(rep["reclaimable_bytes"], len(CONTENT))
        self.assertEqual(g["sha256"], sha(CONTENT))

    def test_01b_standard_folder_beats_legacy_alias(self):
        self.w(self.sd1 / "Roms/PS/Game.chd")
        std = self.w(self.sd1 / "Roms/PSX/Game.chd")
        g = self.find()["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), std)
        self.assertIn("chuẩn", g["keep_reason"])

    def test_02_copy_with_save_is_kept_even_if_other_path_is_shorter(self):
        a = self.w(self.sd1 / "Roms/GBA/Pokemon (USA).gba")
        self.w(self.sd1 / "Saves/Pokemon (USA).srm", b"save")
        b = self.w(self.sd1 / "Roms/GBA/B.gba")
        self.assertLess(len(str(b)), len(str(a)))
        g = self.find()["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), a)
        self.assertTrue(g["keep"]["has_save"])
        self.assertEqual([Path(r["path"]) for r in g["remove"]], [b])
        self.assertFalse(g["remove"][0]["has_save"])
        self.assertIn("save", g["keep_reason"])

    def test_03_same_size_different_content_is_not_a_duplicate(self):
        self.w(self.sd1 / "Roms/GBA/X.gba", b"A" * 500)
        self.w(self.sd2 / "Roms/GBA/X.gba", b"B" * 500)
        rep = self.find()
        self.assertEqual(rep["groups"], [])
        self.assertEqual(rep["reclaimable_bytes"], 0)
        self.assertEqual(rep["hashed_files"], 2)  # they WERE compared, and rejected

    def test_06_bin_referenced_by_cue_is_never_proposed(self):
        # 32X lists .bin as a ROM extension, so only the cue reference protects it.
        for card in (self.sd1, self.sd2):
            self.w(card / "Roms/32X/Game.bin", b"BINDATA" * 100)
            self.w(card / "Roms/32X/Game.cue", b'FILE "Game.bin" BINARY\n  TRACK 01 MODE1/2352\n')
        # control: an unreferenced identical pair IS found, so the test is not vacuous
        self.w(self.sd1 / "Roms/32X/Loose.bin", b"LOOSE" * 100)
        self.w(self.sd2 / "Roms/32X/Loose.bin", b"LOOSE" * 100)
        rep = self.find()
        proposed = [Path(x["path"]).name for g in rep["groups"] for x in [g["keep"]] + g["remove"]]
        self.assertNotIn("Game.bin", proposed)
        self.assertIn("Loose.bin", proposed)
        self.assertTrue(any(m["name"] == "Game.cue" for m in rep["skipped_multifile"]))

    def test_06b_referenced_file_is_kept_and_loose_copy_removed(self):
        # The referenced copy sits on the card with LESS space, so only the
        # "referenced => must keep" rule (not free space) can pick it.
        wrapped = self.w(self.sd1 / "Roms/PSX/Game (USA).chd", b"CHD" * 300)
        m3u = self.w(self.sd1 / "Roms/PSX/Game (USA).m3u", b"Game (USA).chd\n")
        loose = self.w(self.sd2 / "Roms/PSX/Game (USA).chd", b"CHD" * 300)
        rep = self.find()
        self.assertEqual(len(rep["groups"]), 1)
        g = rep["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), wrapped)
        self.assertEqual([Path(r["path"]) for r in g["remove"]], [loose])
        self.assertIn("playlist", g["keep_reason"])
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [str(loose)])
        self.assertTrue(wrapped.exists() and m3u.exists())
        self.assertIn("Game (USA).chd", m3u.read_text())  # the playlist still points at an existing file

    def test_06e_loose_copy_with_save_survives_forced_keeper(self):
        """Bản lẻ có save không bao giờ bị xoá, kể cả khi bản kia bị ép làm bản giữ.

        Tái hiện ca thật 2026-10-02: Chrono Trigger trên SD1 có save, bản SD2 nằm
        trong .m3u nên bị ép giữ, và bản đang chơi suýt bị đề xuất xoá.
        """
        self.w(self.sd2 / "Roms/PSX/CT (Rev 1).chd", b"CHD" * 300)
        self.w(self.sd2 / "Roms/PSX/CT.m3u", b"CT (Rev 1).chd\n")
        played = self.w(self.sd1 / "Roms/PSX/CT (Rev 1).chd", b"CHD" * 300)
        self.w(self.sd1 / "Saves/CT (Rev 1).srm", b"PROGRESS")
        for pref in ("auto", "primary", "secondary"):
            rep = self.find(prefer_card=pref)
            removed = [Path(r["path"]) for g in rep["groups"] for r in g["remove"]]
            self.assertNotIn(played, removed, f"bản có save bị đề xuất xoá (prefer_card={pref})")
        self.assertTrue(played.exists())

    def test_06f_execute_refuses_duplicate_victim_that_has_a_save(self):
        """Tầng thực thi cũng từ chối, phòng khi kế hoạch được dựng sai ở đâu đó."""
        keep = self.w(self.sd2 / "Roms/GBA/G.gba", b"ROM" * 300)
        victim = self.w(self.sd1 / "Roms/GBA/G.gba", b"ROM" * 300)
        self.w(self.sd1 / "Saves/G.srm", b"PROGRESS")
        res = DD.execute_deletion([victim], self.ud, "duplicate", kept_path=keep,
                                  expected_sha256=sha(b"ROM" * 300), primary_path=self.sd1,
                                  secondary_path=self.sd2)
        self.assertEqual(res["deleted"], [])
        self.assertTrue(victim.exists())

    def test_06c_two_referenced_copies_means_nothing_is_removed(self):
        for card in (self.sd1, self.sd2):
            self.w(card / "Roms/PSX/Game (USA).chd", b"CHD" * 300)
            self.w(card / "Roms/PSX/Game (USA).m3u", b"Game (USA).chd\n")
        rep = self.find()
        self.assertEqual([r for g in rep["groups"] for r in g["remove"]], [])

    def test_06d_execute_refuses_a_victim_that_a_playlist_references(self):
        keep = self.w(self.sd2 / "Roms/PSX/G.chd", b"CHD" * 300)
        victim = self.w(self.sd1 / "Roms/PSX/G.chd", b"CHD" * 300)
        self.w(self.sd1 / "Roms/PSX/G.m3u", b"G.chd\n")
        res = DD.execute_deletion([victim], self.ud, "duplicate", kept_path=keep,
                                  expected_sha256=sha(b"CHD" * 300), primary_path=self.sd1,
                                  secondary_path=self.sd2)
        self.assertEqual(res["deleted"], [])
        self.assertTrue(victim.exists())

    def test_13_prefer_card_chooses_card_when_no_save(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba")
        b = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.assertEqual(Path(self.find(prefer_card="primary")["groups"][0]["keep"]["path"]), a)
        self.assertEqual(Path(self.find(prefer_card="secondary")["groups"][0]["keep"]["path"]), b)
        rep = self.find(compare=True)
        self.assertEqual(rep["compare"]["primary"]["by_card"]["secondary"], len(CONTENT))
        self.assertEqual(rep["compare"]["secondary"]["by_card"]["primary"], len(CONTENT))
        self.assertEqual(rep["reclaimable_by_card"]["primary"] + rep["reclaimable_by_card"]["secondary"],
                         rep["reclaimable_bytes"])

    def test_13b_save_beats_prefer_card(self):
        a = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Saves/Game.srm", b"save")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        g = self.find(prefer_card="secondary")["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), a)
        self.assertIn("dù bạn chọn", g["keep_reason"])
        self.assertIn("save", g["keep_reason"])

    def test_13c_prefer_card_notes_missing_cover_on_chosen_card(self):
        self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Images/GBA/Game.png", b"png")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        g = self.find(prefer_card="secondary")["groups"][0]
        self.assertEqual(g["keep"]["card_id"], "secondary")
        self.assertIn("ảnh bìa", g["keep_reason"])

    def test_10_only_files_with_equal_size_are_hashed(self):
        for i in range(50):
            self.w(self.sd1 / f"Roms/GBA/uniq{i}.gba", b"x" * (1000 + i))
        self.w(self.sd1 / "Roms/GBA/dup1.gba", b"y" * 5000)
        self.w(self.sd2 / "Roms/GBA/dup2.gba", b"y" * 5000)
        rep = self.find()
        self.assertEqual(rep["hashed_files"], 2)
        self.assertEqual(rep["hashed_bytes"], 10000)
        self.assertEqual(len(rep["groups"]), 1)

    def test_10b_hash_budget_skips_groups(self):
        self.w(self.sd1 / "Roms/GBA/a.gba", b"y" * 5000)
        self.w(self.sd2 / "Roms/GBA/b.gba", b"y" * 5000)
        rep = self.find(max_hash_bytes=100)
        self.assertEqual(rep["hashed_files"], 0)
        self.assertEqual(rep["skipped_budget_files"], 2)
        self.assertEqual(rep["groups"], [])


class TestExecuteDeletion(DedupeBase):
    def make_pair(self):
        keep = self.w(self.sd2 / "Roms/GBA/Game.gba")
        victim = self.w(self.sd1 / "Roms/GBA/Game.gba")
        g = self.find()["groups"][0]
        self.assertEqual(Path(g["keep"]["path"]), keep)
        return g, keep, victim

    def test_04_keeper_changed_after_plan_refuses_and_keeps_victim(self):
        g, keep, victim = self.make_pair()
        keep.write_bytes(b"CHANGED AFTER SCAN" * 40)
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [])
        self.assertEqual(len(res["refused"]), 1)
        self.assertTrue(victim.exists())

    def test_04b_victim_changed_after_plan_is_refused(self):
        g, keep, victim = self.make_pair()
        victim.write_bytes(b"DIFFERENT NOW" * 40)
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [])
        self.assertTrue(victim.exists())
        self.assertTrue(keep.exists())

    def test_05_keeper_gone_before_execution_never_deletes_last_copy(self):
        g, keep, victim = self.make_pair()
        keep.unlink()
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [])
        self.assertTrue(victim.exists())
        self.assertEqual(victim.read_bytes(), CONTENT)

    def test_05b_happy_path_deletes_victim_only(self):
        g, keep, victim = self.make_pair()
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [str(victim)])
        self.assertEqual(res["bytes_freed"], len(CONTENT))
        self.assertFalse(victim.exists())
        self.assertTrue(keep.exists())

    def test_08_paths_outside_roms_are_refused(self):
        save = self.w(self.sd1 / "Saves/x.srm", b"save")
        evil = self.w(self.sd1 / "Roms_evil/x.gba")
        outside = self.w(self.tmp / "elsewhere/x.gba")
        res = DD.execute_deletion([save, evil, outside], self.ud, "user_request",
                                  primary_path=self.sd1, secondary_path=self.sd2)
        self.assertEqual(res["deleted"], [])
        self.assertEqual(len(res["refused"]), 3)
        for f in (save, evil, outside):
            self.assertTrue(f.exists())
        plan = DD.plan_rom_deletion(save, self.sd1, self.sd2)
        self.assertFalse(plan["ok"])

    def test_09_ledger_records_sha_and_kept_path_before_delete(self):
        g, keep, victim = self.make_pair()
        self.run_group(g)
        lines = (self.ud / "deletion-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        entries = [json.loads(x) for x in lines]
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["path"], str(victim))
        self.assertEqual(e["sha256"], sha(CONTENT))
        self.assertEqual(e["kept_path"], str(keep))
        self.assertEqual(e["reason"], "duplicate")
        self.assertEqual(e["size"], len(CONTENT))
        self.assertIn("deleted_at", e)

    def test_09b_unwritable_ledger_blocks_deletion(self):
        g, keep, victim = self.make_pair()
        (self.ud / "deletion-ledger.jsonl").mkdir()  # a directory: append must fail
        res = self.run_group(g)
        self.assertEqual(res["deleted"], [])
        self.assertTrue(victim.exists())

    def test_12_quarantine_move_list_and_purge(self):
        g, keep, victim = self.make_pair()
        res = DD.execute_deletion([victim], self.ud, "duplicate", kept_path=keep,
                                  expected_sha256=g["sha256"], primary_path=self.sd1,
                                  secondary_path=self.sd2, quarantine=True)
        self.assertEqual(res["deleted"], [str(victim)])
        self.assertEqual(res["bytes_freed"], 0)
        self.assertFalse(victim.exists())
        lst = DD.list_quarantine(self.ud, self.sd2)
        self.assertEqual(lst["total_files"], 1)
        qfile = Path(lst["batches"][0]["files"][0]["path"])
        self.assertEqual(qfile.read_bytes(), CONTENT)
        # --older-than 1 day must NOT purge a batch created just now
        self.assertEqual(DD.purge_quarantine(self.ud, self.sd2, older_than_days=1)["deleted"], [])
        self.assertTrue(qfile.exists())
        res = DD.purge_quarantine(self.ud, self.sd2)
        self.assertEqual(res["bytes_freed"], len(CONTENT))
        self.assertFalse(qfile.exists())
        self.assertEqual(DD.list_quarantine(self.ud, self.sd2)["total_files"], 0)


class TestPlanRomDeletion(DedupeBase):
    def make_cue_game(self):
        cue = self.w(self.sd1 / "Roms/PSX/Game.cue",
                     b'FILE "Game (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n'
                     b'FILE "Game (Track 2).bin" BINARY\n  TRACK 02 AUDIO\n')
        t1 = self.w(self.sd1 / "Roms/PSX/Game (Track 1).bin", b"T1" * 100)
        t2 = self.w(self.sd1 / "Roms/PSX/Game (Track 2).bin", b"T2" * 100)
        other = self.w(self.sd1 / "Roms/PSX/Unrelated.cue", b'FILE "Unrelated.bin" BINARY\n')
        other_bin = self.w(self.sd1 / "Roms/PSX/Unrelated.bin", b"U" * 100)
        save = self.w(self.sd1 / "Saves/Game.srm", b"my progress")
        state = self.w(self.sd1 / "States/Game.state1", b"state")
        art = self.w(self.sd1 / "Images/PSX/Game.png", b"png")
        return cue, t1, t2, other, other_bin, save, state, art

    def test_07_cue_plan_includes_tracks_and_keeps_saves_on_disk(self):
        cue, t1, t2, other, other_bin, save, state, art = self.make_cue_game()
        plan = DD.plan_rom_deletion(cue, self.sd1, self.sd2)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(sorted(plan["files"]), sorted(str(x) for x in (cue, t1, t2)))
        self.assertEqual(plan["artwork"], [str(art)])
        self.assertEqual(sorted(plan["saves_kept"]), sorted(str(x) for x in (save, state)))
        self.assertTrue(any("save" in w.lower() for w in plan["warnings"]))
        res = DD.execute_deletion(plan["files"], self.ud, "user_request", primary_path=self.sd1,
                                  secondary_path=self.sd2, artwork=plan["artwork"])
        self.assertEqual(res["refused"], [])
        for gone in (cue, t1, t2, art):
            self.assertFalse(gone.exists(), gone)
        for stay in (save, state, other, other_bin):
            self.assertTrue(stay.exists(), stay)
        self.assertEqual(save.read_bytes(), b"my progress")

    def test_07b_track_shared_with_another_playlist_is_not_deleted(self):
        cue, t1, t2, *_ = self.make_cue_game()
        self.w(self.sd1 / "Roms/PSX/Shared.cue", b'FILE "Game (Track 2).bin" BINARY\n')
        plan = DD.plan_rom_deletion(cue, self.sd1, self.sd2)
        self.assertTrue(plan["ok"])
        self.assertNotIn(str(t2), plan["files"])
        self.assertIn(str(t1), plan["files"])
        self.assertTrue(any("Track 2" in w for w in plan["warnings"]))

    def test_07c_m3u_plan_includes_discs_and_their_tracks(self):
        m3u = self.w(self.sd1 / "Roms/PSX/Multi.m3u", b"Multi (Disc 1).cue\nMulti (Disc 2).cue\n")
        files = [m3u]
        for n in (1, 2):
            files.append(self.w(self.sd1 / f"Roms/PSX/Multi (Disc {n}).cue",
                                f'FILE "Multi (Disc {n}).bin" BINARY\n'.encode()))
            files.append(self.w(self.sd1 / f"Roms/PSX/Multi (Disc {n}).bin", b"D" * 50))
        plan = DD.plan_rom_deletion(m3u, self.sd1, self.sd2)
        self.assertEqual(sorted(plan["files"]), sorted(str(x) for x in files))

    def test_07d_bin_referenced_by_cue_cannot_be_deleted_alone(self):
        cue, t1, *_ = self.make_cue_game()
        plan = DD.plan_rom_deletion(t1, self.sd1, self.sd2)
        self.assertFalse(plan["ok"])
        self.assertEqual(plan["files"], [])


class TestCli(DedupeBase):
    def run_cli(self, *args, stdin=subprocess.DEVNULL):
        return subprocess.run(
            [sys.executable, str(ROOT / "engine" / "cli.py"), "--primary", str(self.sd1),
             "--secondary", str(self.sd2), "--userdata", str(self.ud), "--quiet", *args],
            cwd=str(ROOT), stdin=stdin, capture_output=True, text=True, encoding="utf-8")

    def test_11_apply_without_tty_and_without_yes_deletes_nothing(self):
        keep = self.w(self.sd2 / "Roms/GBA/Game.gba")
        victim = self.w(self.sd1 / "Roms/GBA/Game.gba")
        r = self.run_cli("dedupe", "--apply")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(victim.exists())
        self.assertTrue(keep.exists())
        self.assertFalse((self.ud / "deletion-ledger.jsonl").exists())

        # control: the same command with --yes does delete, so the check above can fail
        r = self.run_cli("dedupe", "--apply", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # both temp cards share one disk (free-space tie), so either copy may be the one kept
        self.assertEqual([keep.exists(), victim.exists()].count(True), 1)
        self.assertIn("Rescan Library", r.stdout)

    def test_11b_dedupe_default_is_view_only_and_json_is_valid(self):
        keep = self.w(self.sd2 / "Roms/GBA/Game.gba")
        victim = self.w(self.sd1 / "Roms/GBA/Game.gba")
        r = self.run_cli("dedupe", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data["groups"]), 1)
        self.assertTrue(victim.exists())

    def test_11c_rom_delete_without_yes_non_tty_is_refused(self):
        rom = self.w(self.sd1 / "Roms/GBA/Game.gba")
        r = self.run_cli("rom", "delete", str(rom))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertTrue(rom.exists())


if __name__ == "__main__":
    unittest.main()
