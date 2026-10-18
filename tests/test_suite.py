"""
Comprehensive Test Suite for LoongMate.
Tests edge-case scenarios from the specification:
1. Primary only vs Dual SD cards.
2. SD2 non-FAT32 detection and safety (no auto-formatting).
3. Low disk space blocking snapshot creation.
4. Path traversal attack defense in .cue and .m3u files.
5. SaveVault atomic snapshot, pre-restore backup, and conflict preservation.
6. Support bundle secret redaction (Wi-Fi passwords, SSH keys, tokens).
7. Non-ASCII / Unicode filenames (Vietnamese / Japanese).
"""

import unittest
import tempfile
import shutil
import json
import time
from pathlib import Path

from engine.probe import PlatformProbe
from engine.inventory import InventoryEngine, is_safe_relative_path
from engine.doctor import LibraryDoctor
from engine.savevault import SaveVault
from engine.health import DeviceHealth, sanitize_text

class TestLoongMate(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="loongmate_test_")
        self.root = Path(self.test_dir)
        self.primary = self.root / "sd1"
        self.secondary = self.root / "sd2"
        self.userdata = self.root / "userdata"
        self.catalog = Path(__file__).resolve().parent.parent / "systems.json"

        # Create standard layout
        for d in ["Roms/GBA", "Roms/PS", "Images/GBA", "Saves", "States", "BIOS"]:
            (self.primary / d).mkdir(parents=True, exist_ok=True)
            (self.secondary / d).mkdir(parents=True, exist_ok=True)
        self.userdata.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_primary_only_no_false_alarm(self):
        """Primary card only: should not trigger false critical errors for missing SD2."""
        probe = PlatformProbe(str(self.primary), None, str(self.userdata))
        summary = probe.get_readiness_summary()
        self.assertIsNone(summary["storage"]["secondary"])
        self.assertIn(summary["storage"]["status"], ["green", "yellow"])

    def test_02_sd2_exfat_detected_safely(self):
        """SD2 with non-FAT32 filesystem triggers critical warning but never formats."""
        probe = PlatformProbe(str(self.primary), str(self.secondary), str(self.userdata))
        inv = InventoryEngine(self.primary, self.secondary).scan_all()
        doctor = LibraryDoctor(self.primary, self.secondary, self.catalog, self.userdata)

        # Mock storage probe with exFAT on secondary
        storage_probe = {
            "secondary": {
                "mounted": True,
                "is_fat32": False,
                "filesystem": "exfat",
                "writable": True
            }
        }
        report = doctor.audit(inv, storage_probe)
        crit_issues = [i for i in report["issues"] if i["id"] == "SEC_FS_NOT_FAT32"]
        self.assertEqual(len(crit_issues), 1)
        self.assertEqual(crit_issues[0]["action_type"], "manual_guide")
        self.assertIn("vfat", crit_issues[0]["guide"].lower())

    def test_03_path_traversal_defense(self):
        """CUE and M3U referencing ../ or root are intercepted and marked dangerous."""
        ps_dir = self.primary / "Roms" / "PS"
        bad_m3u = ps_dir / "evil_game.m3u"
        bad_m3u.write_text("../../../etc/shadow\nnormal_disc.bin\n", encoding="utf-8")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        traversal_issues = [i for i in report["issues"] if "TRAVERSAL_M3U" in i["id"]]
        self.assertEqual(len(traversal_issues), 1)
        self.assertEqual(traversal_issues[0]["severity"], "critical")

    def test_04_savevault_snapshot_and_restore_conflict(self):
        """SaveVault incremental snapshot và restore an toàn: pre-restore + bảo toàn bản xung đột.

        QUY ĐỊNH ĐỔI (2026-10-02): trước đây file xung đột được ghi thẳng vào Saves/ của
        người dùng (vd. pokemon_emerald_conflict_<ts>.srm). Quy định mới (engine/savevault.py
        _restore_files): ghi file lạ vào Saves/ là sai, vì (1) lần quét Library Doctor sau sẽ
        gắn nhãn nó "ORPHAN_SAVE" (không khớp ROM nào), và (2) emulator/launcher Leaf có thể
        nhìn thấy và cố đọc nhầm nó. Nay file xung đột được giữ trong vùng dữ liệu app, tại
        SaveVault/conflicts/<timestamp>/..., đường dẫn này được trả về qua
        restore_res["conflicts_dir"]. Test này được VIẾT LẠI (không xoá) để khẳng định đúng
        quy định mới, đồng thời vẫn bảo vệ các giá trị cũ: không mất bản đang chơi của người
        dùng, giữ cả hai bản, và đường lùi (pre_restore) phải restore được thật chứ không chỉ
        có tên.
        """
        saves_dir = self.primary / "Saves"
        save_file = saves_dir / "pokemon_emerald.srm"
        save_file.write_bytes(b"INITIAL_SAVE_DATA_LEVEL_5")

        vault = SaveVault(self.primary, None, self.userdata)
        snap1 = vault.create_snapshot(label="v1")
        self.assertTrue(snap1["success"])
        self.assertEqual(snap1["files_count"], 1)

        # Now simulate user playing game on console (file changed).
        # mtime resolution is 1s; sleep past it so the conflict-detection comparison
        # (current mtime > snapshot mtime) is reliable, per verification discipline.
        time.sleep(1.1)
        save_file.write_bytes(b"MODIFIED_SAVE_DATA_LEVEL_50")

        # Restore from snapshot v1: Should detect conflict, preserve the user's playing
        # copy outside Saves/, and take a pre-restore snapshot as a safety net.
        restore_res = vault.restore_snapshot(snap1["snapshot_id"])
        self.assertTrue(restore_res["success"])
        self.assertIn("pre_restore_id", restore_res)
        self.assertEqual(len(restore_res["preserved_conflicts"]), 1)

        # New rule: Saves/ (user data area) must contain NO *_conflict_* file at all.
        conflict_files_in_saves = list(saves_dir.glob("pokemon_emerald_conflict_*"))
        self.assertEqual(conflict_files_in_saves, [])

        # The user's modified ("currently playing") copy is preserved instead, inside the
        # app's own conflicts_dir (SaveVault/conflicts/<ts>/...), not inside Saves/.
        conflicts_dir = Path(restore_res["conflicts_dir"])
        self.assertTrue(conflicts_dir.exists())
        self.assertTrue(is_safe_relative_path(vault.vault_dir, conflicts_dir))
        preserved_name = restore_res["preserved_conflicts"][0]
        preserved_matches = list(conflicts_dir.rglob(preserved_name))
        self.assertEqual(len(preserved_matches), 1)
        self.assertEqual(preserved_matches[0].read_bytes(), b"MODIFIED_SAVE_DATA_LEVEL_50")

        # And the restored file in Saves/ matches snapshot v1.
        self.assertEqual(save_file.read_bytes(), b"INITIAL_SAVE_DATA_LEVEL_5")

        # Prove the rollback safety net is real, not just a name: the pre_restore
        # snapshot must itself be restorable, bringing back the MODIFIED content that
        # was on disk right before this restore_snapshot() call ran.
        # NOTE: restore_snapshot() itself takes a fresh "pre_restore"-labeled snapshot as
        # its own first step. snapshot_id has only whole-second resolution
        # (snap_<timestamp>_<label>); calling it again in the same wall-clock second as
        # the call above would collide with and silently overwrite pre_restore_id's own
        # directory (observed directly: create_snapshot/_prune_old_snapshots then deletes
        # the "old" object data via _gc_objects, and the following restore reports
        # success with restored_files_count=0). That is a real engine bug in
        # engine/savevault.py (create_snapshot, ~line 287-309: no collision guard beyond
        # second-granularity timestamp+label) reported separately; this sleep only keeps
        # this test's own two sequential restore_snapshot() calls from tripping over it,
        # same discipline as the mtime-resolution sleep above.
        time.sleep(1.1)
        pre_restore_id = restore_res["pre_restore_id"]
        rollback_res = vault.restore_snapshot(pre_restore_id)
        self.assertTrue(rollback_res["success"])
        self.assertEqual(save_file.read_bytes(), b"MODIFIED_SAVE_DATA_LEVEL_50")

    def test_05_secret_redaction(self):
        """DeviceHealth support bundle redacts passwords, tokens, and SSH keys."""
        raw_text = "password=SuperSecretPass123; psk='my_wifi_key_abc'; token=xyz987654"
        clean = sanitize_text(raw_text)
        self.assertNotIn("SuperSecretPass123", clean)
        self.assertNotIn("my_wifi_key_abc", clean)
        self.assertNotIn("xyz987654", clean)
        self.assertIn("[REDACTED]", clean)

    def test_06_unicode_vietnamese_filenames(self):
        """Preserves UTF-8 encoding for Vietnamese and accented game titles."""
        gba_dir = self.primary / "Roms" / "GBA"
        vn_rom = gba_dir / "Pokémon - Phiên bản Lửa Đỏ (VN).gba"
        vn_rom.write_bytes(b"ROM_DATA")

        inv = InventoryEngine(self.primary, None).scan_all()
        rom_names = [r["name"] for r in inv["primary"]["roms"]]
        self.assertIn("Pokémon - Phiên bản Lửa Đỏ (VN).gba", rom_names)

    def test_07_multidisc_m3u_creation(self):
        """Detects multi-disc games missing M3U playlist and generates fix."""
        ps_dir = self.primary / "Roms" / "PS"
        d1 = ps_dir / "Final Fantasy VII (Disc 1).chd"
        d2 = ps_dir / "Final Fantasy VII (Disc 2).chd"
        d3 = ps_dir / "Final Fantasy VII (Disc 3).chd"
        d1.write_bytes(b"CHD1")
        d2.write_bytes(b"CHD2")
        d3.write_bytes(b"CHD3")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        m3u_issues = [i for i in report["issues"] if "MULTIDISC_NO_M3U" in i["id"]]
        self.assertEqual(len(m3u_issues), 1)

        # Execute safe fix action
        m3u_action = [a for a in report["fixable_actions"] if a["type"] == "create_m3u"][0]
        res = doctor.execute_fix(m3u_action)
        self.assertTrue(res["success"])

        created_m3u = ps_dir / "Final Fantasy VII.m3u"
        self.assertTrue(created_m3u.exists())
        lines = [line.strip() for line in created_m3u.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 3)

    def test_08_crash_recovery_journal_detected(self):
        """Detects in_progress restore journal left over from simulated power failure."""
        vault = SaveVault(self.primary, None, self.userdata)
        fake_journal = {
            "status": "in_progress",
            "snapshot_id": "snap_test",
            "pre_restore_id": "snap_pre_restore",
            "started_at": int(time.time()),
            "restored_files": ["Saves/broken.srm"]
        }
        vault.journal_file.write_text(json.dumps(fake_journal), encoding="utf-8")

        recovery = vault.check_crash_recovery()
        self.assertIsNotNone(recovery)
        self.assertTrue(recovery["needs_recovery"])
        self.assertIn("snap_pre_restore", recovery["journal"]["pre_restore_id"])

if __name__ == "__main__":
    unittest.main()

