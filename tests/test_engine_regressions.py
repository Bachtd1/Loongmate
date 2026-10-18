"""
Regression test suite for LoongMate engine fixes (2026-10-02 pass).
Each test below exercises real behavior (no source-grepping, no pinning to dead
constants) for a bug that was specifically identified and fixed in this pass:
inventory path-boundary checks, probe mount matching, no-fabrication hardware
probes, read-only scanning, SaveVault incremental hashing / traversal defense /
legacy-format compatibility / crash rollback / corruption detection / disk-space
guard, Library Doctor's .cue traversal + systems.json folder/alias/catalog
handling, and DeviceHealth secret redaction / privacy mode / no-fabrication
support bundles.
"""

import os
import json
import time
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from engine.probe import PlatformProbe
from engine.inventory import InventoryEngine, is_safe_relative_path, compute_sha256
from engine.doctor import LibraryDoctor
from engine.savevault import SaveVault
from engine.health import DeviceHealth, sanitize_text
import engine.savevault as savevault_module


class TestEngineRegressions(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="loongmate_regress_")
        self.root = Path(self.test_dir)
        self.primary = self.root / "sd1"
        self.secondary = self.root / "sd2"
        self.userdata = self.root / "userdata"
        self.catalog = Path(__file__).resolve().parent.parent / "systems.json"
        self.primary.mkdir(parents=True, exist_ok=True)
        self.secondary.mkdir(parents=True, exist_ok=True)
        self.userdata.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # 1. Path boundary (is_safe_relative_path)
    # ------------------------------------------------------------------
    def test_r01_path_boundary_roms_vs_roms_evil(self):
        """is_safe_relative_path không coi 'Roms_evil' là nằm trong 'Roms' (biên theo thành phần đường dẫn, không phải string prefix)."""
        roms = self.primary / "Roms"
        roms.mkdir(parents=True, exist_ok=True)
        evil = self.primary / "Roms_evil"
        evil.mkdir(parents=True, exist_ok=True)
        evil_file = evil / "x.rom"
        evil_file.write_bytes(b"x")
        self.assertFalse(is_safe_relative_path(roms, evil_file))

        real_file = roms / "GBA" / "b.gba"
        real_file.parent.mkdir(parents=True, exist_ok=True)
        real_file.write_bytes(b"ok")
        self.assertTrue(is_safe_relative_path(roms, real_file))

    # ------------------------------------------------------------------
    # 2. Filesystem mount matching (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r02_match_mount_longest_prefix_and_boundary_safe(self):
        """_match_mount chọn mount dài nhất theo biên thành phần đường dẫn, và kết quả
        không đổi khi đảo thứ tự dòng trong bảng mounts (lỗi làm SD2 luôn PASS âm thầm)."""
        probe = PlatformProbe(str(self.primary), str(self.secondary), str(self.userdata))

        entries = [
            ("/", "ext4", ["rw"]),
            ("/mnt/SDCARD", "vfat", ["rw"]),
            ("/mnt/SDCARD2", "exfat", ["ro"]),
        ]
        entries_reversed = list(reversed(entries))

        for table in (entries, entries_reversed):
            m_root = probe._match_mount("/mnt/other_thing", table)
            m_sd1 = probe._match_mount("/mnt/SDCARD", table)
            m_sd2 = probe._match_mount("/mnt/SDCARD2", table)

            self.assertIsNotNone(m_root)
            self.assertEqual(m_root[1], "ext4")  # falls back to "/" for unrelated absolute paths
            self.assertIsNotNone(m_sd1)
            self.assertEqual(m_sd1[1], "vfat")
            self.assertIsNotNone(m_sd2)
            self.assertEqual(m_sd2[1], "exfat")
            self.assertIn("ro", m_sd2[2])

    # ------------------------------------------------------------------
    # 3. No hardware fabrication
    # ------------------------------------------------------------------
    def test_r03_battery_and_thermal_not_fabricated_without_sysfs(self):
        """Không có sysfs pin/nhiệt (máy test): get_battery_info/get_thermal_info trả
        available=False và percent/current_celsius=None, không bịa số liệu."""
        if Path("/sys/class/power_supply").exists() or Path("/sys/class/thermal").exists():
            self.skipTest("Máy chạy test này có /sys/class/power_supply hoặc /sys/class/thermal thật; không khẳng định bừa available=False.")

        probe = PlatformProbe(str(self.primary), None, str(self.userdata))
        bat = probe.get_battery_info()
        self.assertFalse(bat["available"])
        self.assertIsNone(bat["percent"])

        therm = probe.get_thermal_info()
        self.assertFalse(therm["available"])
        self.assertIsNone(therm["current_celsius"])

    # ------------------------------------------------------------------
    # 4. Read-only scanning never mutates the card
    # ------------------------------------------------------------------
    def test_r04_scan_is_read_only_no_mtime_or_content_change(self):
        """get_storage_info() và scan_all() không được thay đổi mtime/nội dung hoặc tạo
        file mới trên thẻ (tiêu chí nghiệm thu: 'Scan read-only không thay đổi mtime')."""
        rom = self.primary / "Roms" / "GBA" / "game.gba"
        rom.parent.mkdir(parents=True, exist_ok=True)
        rom.write_bytes(b"ROMDATA")
        save = self.primary / "Saves" / "game.srm"
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(b"SAVEDATA")

        def snapshot(root):
            data = {}
            for p in root.rglob("*"):
                if p.is_file():
                    st = p.stat()
                    data[str(p)] = (st.st_size, st.st_mtime)
            return data

        before = snapshot(self.primary)

        probe = PlatformProbe(str(self.primary), str(self.secondary), str(self.userdata))
        probe.get_storage_info(self.primary)
        InventoryEngine(self.primary, self.secondary).scan_all()

        after = snapshot(self.primary)
        self.assertEqual(before, after)

    # ------------------------------------------------------------------
    # 5. Incremental backup hashing (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r05_incremental_snapshot_rehash_count_and_vault_growth(self):
        """Snapshot kế tiếp khi không đổi gì: 0 lần gọi compute_sha256 và vault tăng
        dưới 100KB; đổi đúng 1 file thì đúng 1 lần hash. Dùng số đo, không hằng số cứng."""
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (saves_dir / f"save_{i}.srm").write_bytes(os.urandom(2000))

        vault = SaveVault(self.primary, None, self.userdata)
        snap1 = vault.create_snapshot(label="s1")
        self.assertTrue(snap1["success"])

        def vault_total_bytes():
            total = 0
            for p in vault.vault_dir.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
            return total

        size_after_1 = vault_total_bytes()

        call_count = {"n": 0}
        orig_sha256 = compute_sha256

        def counting_sha256(path, *args, **kwargs):
            call_count["n"] += 1
            return orig_sha256(path, *args, **kwargs)

        # QUY ĐỊNH ĐỔI 2026-10-02: cache size+mtime chỉ được tin với file có
        # mtime CŨ HƠN HẲN lần quét trước. File được ghi trong đúng giây diễn ra
        # lần quét trước vẫn "chưa chắc chắn" nên phải băm lại một lần — nếu
        # không, một save cùng kích thước (.srm vốn cố định kích thước) sửa
        # trong cùng giây sẽ bị ghi hash cũ và bản sao lưu KHÔNG chứa dữ liệu
        # thật (xem test_r20). Vì vậy snapshot#2 ngay sau snapshot#1 được phép
        # băm lại; đến snapshot#3 thì file đã "yên" và phải miễn phí hoàn toàn.
        time.sleep(1.1)
        snap2 = vault.create_snapshot(label="s2")
        self.assertTrue(snap2["success"])

        time.sleep(1.1)
        with mock.patch.object(savevault_module, "compute_sha256", side_effect=counting_sha256):
            snap2b = vault.create_snapshot(label="s2b")
        self.assertTrue(snap2b["success"])
        self.assertEqual(call_count["n"], 0,
                         "Ở trạng thái ổn định, không file nào đổi thì không được hash lại file nào")

        size_after_2 = vault_total_bytes()
        growth_unchanged = size_after_2 - size_after_1
        self.assertLess(growth_unchanged, 100 * 1024)

        time.sleep(1.1)
        (saves_dir / "save_0.srm").write_bytes(os.urandom(2000))
        call_count["n"] = 0
        with mock.patch.object(savevault_module, "compute_sha256", side_effect=counting_sha256):
            snap3 = vault.create_snapshot(label="s3")
        self.assertTrue(snap3["success"])
        self.assertEqual(call_count["n"], 1, "Đổi đúng 1 file thì phải hash lại đúng 1 file")

    # ------------------------------------------------------------------
    # 6. snapshot_id traversal defense (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r06_snapshot_id_traversal_rejected(self):
        """preview_restore chặn snapshot_id traversal ('../../../x', '..', 'a/b', '');
        create_snapshot(label='../../evil') không tạo path nào ngoài snapshots/objects."""
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        (saves_dir / "a.srm").write_bytes(b"data")

        vault = SaveVault(self.primary, None, self.userdata)
        vault.create_snapshot(label="base")

        for bad_id in ["../../../x", "..", "a/b", ""]:
            res = vault.preview_restore(bad_id)
            self.assertIn("error", res, f"snapshot_id={bad_id!r} phải bị từ chối")
            self.assertEqual(res["error"], "INVALID_SNAPSHOT_ID")

        before = set(self.root.rglob("*"))
        vault.create_snapshot(label="../../evil")
        after = set(self.root.rglob("*"))
        new_paths = after - before

        self.assertTrue(new_paths, "create_snapshot lẽ ra phải tạo ít nhất manifest/object mới")
        for p in new_paths:
            self.assertTrue(
                is_safe_relative_path(vault.snapshots_dir, p) or is_safe_relative_path(vault.objects_dir, p),
                f"create_snapshot với label độc hại đã tạo path ngoài snapshots/objects: {p}"
            )

    # ------------------------------------------------------------------
    # 7. Backward compatibility with legacy snapshot layout
    # ------------------------------------------------------------------
    def test_r07_restore_legacy_data_layout_snapshot(self):
        """restore_snapshot vẫn đọc được snapshot định dạng CŨ (data/<rel>, không có khóa
        'layout') để không mất dữ liệu người dùng đã sao lưu từ trước bản object-store."""
        vault = SaveVault(self.primary, None, self.userdata)
        snap_id = "snap_legacy_test"
        snap_dir = vault.snapshots_dir / snap_id
        data_dir = snap_dir / "data" / "Saves"
        data_dir.mkdir(parents=True, exist_ok=True)
        legacy_file = data_dir / "x.srm"
        legacy_file.write_bytes(b"LEGACY_BYTES")

        manifest = {
            "snapshot_id": snap_id,
            "committed": True,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": int(time.time()),
            "total_files": 1,
            "total_size_bytes": legacy_file.stat().st_size,
            "files": {
                "Saves/x.srm": {
                    "card": "primary",
                    "adapter": "retroarch_standard",
                    "rel_path": "Saves/x.srm",
                    "absolute_path": str(self.primary / "Saves" / "x.srm"),
                    "size": legacy_file.stat().st_size,
                    "mtime": int(time.time()),
                    "sha256": compute_sha256(legacy_file),
                }
            }
        }
        (snap_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        res = vault.restore_snapshot(snap_id)
        self.assertTrue(res["success"])
        restored = self.primary / "Saves" / "x.srm"
        self.assertEqual(restored.read_bytes(), b"LEGACY_BYTES")

    # ------------------------------------------------------------------
    # 8. Crash rollback
    # ------------------------------------------------------------------
    def test_r08_rollback_from_journal_restores_pre_restore_bytes(self):
        """rollback_from_journal() đưa file về đúng bytes của bản pre-restore sau khi
        mô phỏng mất điện giữa restore; check_crash_recovery() báo can_rollback=True."""
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        f = saves_dir / "game.srm"
        f.write_bytes(b"ORIGINAL_BEFORE_RESTORE")

        vault = SaveVault(self.primary, None, self.userdata)
        pre = vault.create_snapshot(label="pre")
        self.assertTrue(pre["success"])

        time.sleep(1.1)
        f.write_bytes(b"PARTIALLY_RESTORED_BAD_DATA")

        journal = {
            "status": "in_progress",
            "snapshot_id": "some_other_snapshot_id",
            "pre_restore_id": pre["snapshot_id"],
            "started_at": int(time.time()),
            "planned_files": ["Saves/game.srm"],
            "restored_files": ["Saves/game.srm"],
        }
        vault.journal_file.write_text(json.dumps(journal), encoding="utf-8")

        recovery = vault.check_crash_recovery()
        self.assertIsNotNone(recovery)
        self.assertTrue(recovery["needs_recovery"])
        self.assertTrue(recovery["can_rollback"])

        res = vault.rollback_from_journal()
        self.assertTrue(res["success"])
        self.assertEqual(f.read_bytes(), b"ORIGINAL_BEFORE_RESTORE")

    # ------------------------------------------------------------------
    # 9. verify_snapshot corruption detection
    # ------------------------------------------------------------------
    def test_r09_verify_snapshot_detects_corrupted_referenced_object(self):
        """verify_snapshot phát hiện hỏng dữ liệu khi sửa đúng object mà snapshot tham
        chiếu (tra theo sha256 trong manifest, không lấy object đầu tiên gặp)."""
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        (saves_dir / "a.srm").write_bytes(b"AAAA_CONTENT")
        (saves_dir / "b.srm").write_bytes(b"BBBB_CONTENT_DIFFERENT")

        vault = SaveVault(self.primary, None, self.userdata)
        snap = vault.create_snapshot(label="verify_test")
        self.assertTrue(snap["success"])

        ok_check = vault.verify_snapshot(snap["snapshot_id"])
        self.assertTrue(ok_check["ok"])

        manifest = json.loads((Path(snap["path"]) / "manifest.json").read_text(encoding="utf-8"))
        target_rel = "Saves/a.srm"
        target_hash = manifest["files"][target_rel]["sha256"]
        obj_path = vault.objects_dir / target_hash[:2] / target_hash
        self.assertTrue(obj_path.exists(), "Object tương ứng với hash trong manifest phải tồn tại")

        data = bytearray(obj_path.read_bytes())
        data[0] ^= 0xFF
        obj_path.write_bytes(bytes(data))

        bad_check = vault.verify_snapshot(snap["snapshot_id"])
        self.assertFalse(bad_check["ok"])
        self.assertIn(target_rel, bad_check["hash_mismatch"])

    # ------------------------------------------------------------------
    # 10. Disk-space guard (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r10_create_snapshot_blocked_on_low_disk_space(self):
        """create_snapshot từ chối khi dung lượng trống sau khi ghi sẽ dưới 200MB, không
        tạo snapshot nào; trường hợp đủ chỗ thì chạy được bình thường."""
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        (saves_dir / "big.srm").write_bytes(os.urandom(1024))

        vault = SaveVault(self.primary, None, self.userdata)
        real_disk_usage = shutil.disk_usage

        def fake_low(path):
            real = real_disk_usage(path)
            return real._replace(free=199 * 1024 * 1024)

        with mock.patch("shutil.disk_usage", side_effect=fake_low):
            res = vault.create_snapshot(label="blocked")

        self.assertFalse(res["success"])
        self.assertEqual(res["error"], "DISK_LOW_SPACE")
        self.assertEqual(vault.list_snapshots(), [])

        res2 = vault.create_snapshot(label="ok_space")
        self.assertTrue(res2["success"])

    # ------------------------------------------------------------------
    # 11. .cue traversal without quotes (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r11_cue_traversal_detected_unquoted_and_quoted(self):
        """.cue có path traversal KHÔNG ngoặc kép (FILE ../../x BINARY) vẫn bị phát hiện,
        không chỉ dạng có ngoặc kép (ca này trước đây lọt lưới)."""
        ps_dir = self.primary / "Roms" / "PS"
        ps_dir.mkdir(parents=True, exist_ok=True)

        unquoted = ps_dir / "evil_unquoted.cue"
        unquoted.write_text("FILE ../../../../etc/passwd BINARY\n", encoding="utf-8")
        quoted = ps_dir / "evil_quoted.cue"
        quoted.write_text('FILE "../../../../etc/passwd" BINARY\n', encoding="utf-8")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        traversal_cue = [i for i in report["issues"] if "TRAVERSAL_CUE" in i["id"] and i["severity"] == "critical"]
        flagged_files = {i["file_path"] for i in traversal_cue}
        self.assertIn(str(unquoted), flagged_files, "File .cue traversal KHÔNG ngoặc kép phải bị phát hiện")
        self.assertIn(str(quoted), flagged_files, "File .cue traversal CÓ ngoặc kép phải bị phát hiện")

    # ------------------------------------------------------------------
    # 12. Leaf system folders: valid ones not flagged unknown / no quarantine
    # ------------------------------------------------------------------
    def test_r12_known_leaf_system_folders_not_flagged_unknown(self):
        """Thư mục hệ máy Leaf thật nhưng không kiểm phần mở rộng (PORTS, PICO8, DOS, GW,
        PC98, EASYRPG) không bị báo UNKNOWN_SYSTEM_FOLDER và không sinh quarantine;
        thư mục bịa (NOTASYSTEM) thì bị báo."""
        valid_folders = ["PORTS", "PICO8", "DOS", "GW", "PC98", "EASYRPG"]
        for folder in valid_folders:
            d = self.primary / "Roms" / folder
            d.mkdir(parents=True, exist_ok=True)
            (d / "game1.zip").write_bytes(b"DATA")

        fake_dir = self.primary / "Roms" / "NOTASYSTEM"
        fake_dir.mkdir(parents=True, exist_ok=True)
        (fake_dir / "game.rom").write_bytes(b"DATA")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        unknown_ids = {i["id"] for i in report["issues"] if i["id"].startswith("UNKNOWN_SYSTEM_FOLDER_")}
        for folder in valid_folders:
            self.assertNotIn(f"UNKNOWN_SYSTEM_FOLDER_{folder}", unknown_ids)
        self.assertIn("UNKNOWN_SYSTEM_FOLDER_NOTASYSTEM", unknown_ids)

        quarantined_paths = {a["path"] for a in report["fixable_actions"] if a["type"] == "quarantine_file"}
        for folder in valid_folders:
            self.assertNotIn(str(self.primary / "Roms" / folder / "game1.zip"), quarantined_paths)

    def test_r13_legacy_alias_folder_flagged_and_extension_checked(self):
        """Thư mục dùng tên cũ (Roms/PS, alias của PSX) sinh LEGACY_SYSTEM_FOLDER và vẫn
        được kiểm phần mở rộng theo hệ chính danh (PSX)."""
        ps_dir = self.primary / "Roms" / "PS"
        ps_dir.mkdir(parents=True, exist_ok=True)
        good = ps_dir / "game.chd"
        good.write_bytes(b"DATA")
        bad = ps_dir / "game.exe"
        bad.write_bytes(b"DATA")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        legacy_ids = {i["id"] for i in report["issues"] if i["id"].startswith("LEGACY_SYSTEM_FOLDER_")}
        self.assertIn("LEGACY_SYSTEM_FOLDER_PS", legacy_ids)

        invalid_ext = [i for i in report["issues"] if i["id"].startswith("INVALID_EXT_PS_")]
        self.assertEqual(len(invalid_ext), 1)
        self.assertEqual(invalid_ext[0]["file_path"], str(bad))

    # ------------------------------------------------------------------
    # 13. Broken catalog surfaces loudly
    # ------------------------------------------------------------------
    def test_r14_catalog_unavailable_reports_critical(self):
        """systems.json hỏng (JSON sai cú pháp) phát sinh CATALOG_UNAVAILABLE mức
        critical thay vì nuốt lỗi im lặng và vô hiệu hoá nửa bộ kiểm tra."""
        bad_catalog = self.root / "broken_systems.json"
        bad_catalog.write_text("{ this is not valid json !!!", encoding="utf-8")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, bad_catalog, self.userdata)
        report = doctor.audit(inv, {})

        cat_issues = [i for i in report["issues"] if i["id"] == "CATALOG_UNAVAILABLE"]
        self.assertEqual(len(cat_issues), 1)
        self.assertEqual(cat_issues[0]["severity"], "critical")

    # ------------------------------------------------------------------
    # 14. Secret redaction coverage
    # ------------------------------------------------------------------
    def test_r15_sanitize_text_redacts_all_secret_shapes(self):
        """sanitize_text che hết Authorization Bearer <jwt>, wpa_passphrase <ssid> <pass>,
        api_key:, secret=, password=, psk=, và khối -----BEGIN...PRIVATE KEY-----."""
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"
        values = {
            "jwt": jwt,
            "ssid": "MyHomeWifiSSID",
            "wifi_pass": "SuperWifiPass99",
            "apikey": "sk_live_abcdef1234567890",
            "secretval": "topsecretvalue42",
            "pwd": "MyPassword123X",
            "pskval": "pskvalue999Y",
            "key_blob": "FAKEKEYDATABLOB",
        }
        raw = (
            f"Authorization: Bearer {values['jwt']}\n"
            f"wpa_passphrase {values['ssid']} {values['wifi_pass']}\n"
            f"api_key: {values['apikey']}\n"
            f"secret={values['secretval']}\n"
            f"password={values['pwd']}\n"
            f"psk={values['pskval']}\n"
            f"-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAL{values['key_blob']}FAKE\n-----END RSA PRIVATE KEY-----\n"
        )
        clean = sanitize_text(raw)
        for label, value in values.items():
            self.assertNotIn(value, clean, f"Giá trị bí mật '{label}' vẫn còn trong kết quả sau sanitize_text")
        self.assertNotIn("BEGIN RSA PRIVATE KEY", clean)

    # ------------------------------------------------------------------
    # 15. Privacy mode on a real ZIP (REQUIRED red/green proof)
    # ------------------------------------------------------------------
    def test_r16_privacy_mode_hides_filename_in_every_zip_member(self):
        """Chế độ riêng tư ẩn tên ROM nhạy cảm trong TẤT CẢ file của gói ZIP (duyệt
        namelist()), không chỉ library_issues.json."""
        gba_dir = self.primary / "Roms" / "GBA"
        gba_dir.mkdir(parents=True, exist_ok=True)
        (gba_dir / "My Private Game.xyz").write_bytes(b"ROM_DATA")

        inv = InventoryEngine(self.primary, None).scan_all()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        report = doctor.audit(inv, {})

        probe = PlatformProbe(str(self.primary), None, str(self.userdata))
        readiness = probe.get_readiness_summary()

        health = DeviceHealth(self.primary, None, self.userdata)
        bundle_path = health.export_support_bundle(readiness, report, privacy_mode=True)

        with zipfile.ZipFile(bundle_path) as z:
            names = z.namelist()
            self.assertTrue(names)
            for name in names:
                content = z.read(name).decode("utf-8", errors="ignore")
                self.assertNotIn("My Private Game", content, f"Tên ROM nhạy cảm lộ trong {name}")

    # ------------------------------------------------------------------
    # 16. Support bundle never fabricates data
    # ------------------------------------------------------------------
    def test_r17_support_bundle_does_not_fabricate_battery_or_mounts(self):
        """Gói hỗ trợ không bịa số liệu (vd. 'capacity 88%', mật khẩu bịa) trong logs.txt,
        và không bịa dòng mount khi máy không có /proc/mounts."""
        probe = PlatformProbe(str(self.primary), None, str(self.userdata))
        readiness = probe.get_readiness_summary()
        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        inv = InventoryEngine(self.primary, None).scan_all()
        report = doctor.audit(inv, {})

        health = DeviceHealth(self.primary, None, self.userdata)
        bundle_path = health.export_support_bundle(readiness, report, privacy_mode=True)

        with zipfile.ZipFile(bundle_path) as z:
            logs_text = z.read("logs.txt").decode("utf-8", errors="ignore")
            mounts_text = z.read("mounts.txt").decode("utf-8", errors="ignore")

        self.assertNotIn("capacity 88%", logs_text)
        self.assertNotIn("supersecretpassword123", logs_text)
        if not Path("/proc/mounts").exists():
            self.assertNotIn("/dev/mmcblk0p1 on", mounts_text)

    # ------------------------------------------------------------------
    # 17. execute_fix blocks paths outside allowed roots
    # ------------------------------------------------------------------
    def test_r18_execute_fix_rejects_path_outside_allowed_roots(self):
        """execute_fix từ chối đường dẫn ngoài thẻ/userdata; file đó vẫn còn nguyên trên đĩa."""
        outside_dir = self.root / "outside_everything"
        outside_dir.mkdir(parents=True, exist_ok=True)
        outside_file = outside_dir / "not_mine.rom"
        outside_file.write_bytes(b"ORIGINAL_CONTENT")

        doctor = LibraryDoctor(self.primary, None, self.catalog, self.userdata)
        action = {"type": "quarantine_file", "path": str(outside_file), "label": "nope"}
        res = doctor.execute_fix(action)

        self.assertFalse(res["success"])
        self.assertTrue(outside_file.exists())
        self.assertEqual(outside_file.read_bytes(), b"ORIGINAL_CONTENT")

    # ------------------------------------------------------------------
    # 18. Secondary card pulled mid-scan
    # ------------------------------------------------------------------
    def test_r19_scan_with_missing_secondary_path_does_not_raise(self):
        """secondary_path trỏ tới thư mục không tồn tại (thẻ phụ rút giữa chừng): Primary
        vẫn quét đủ, không ném ngoại lệ (Ma trận kiểm thử: 'SD2 tháo giữa scan')."""
        rom = self.primary / "Roms" / "GBA" / "game.gba"
        rom.parent.mkdir(parents=True, exist_ok=True)
        rom.write_bytes(b"ROMDATA")

        missing_secondary = self.root / "sd2_pulled_out"  # deliberately never created

        try:
            inv = InventoryEngine(self.primary, missing_secondary).scan_all()
        except Exception as e:
            self.fail(f"scan_all() ném ngoại lệ khi secondary bị rút giữa chừng: {e}")

        self.assertEqual(len(inv["primary"]["roms"]), 1)
        self.assertIsNotNone(inv["secondary"])
        self.assertEqual(inv["secondary"]["roms"], [])

        storage = PlatformProbe(str(self.primary), str(missing_secondary), str(self.userdata)).get_storage_info(missing_secondary)
        self.assertFalse(storage["mounted"])

    # ------------------------------------------------------------------
    # 20. Save đổi CÙNG KÍCH THƯỚC trong CÙNG GIÂY vẫn phải được sao lưu đúng
    # ------------------------------------------------------------------
    def test_r20_same_size_same_second_change_is_not_missed(self):
        """Save sửa trong cùng giây với lần quét trước, giữ nguyên kích thước,
        vẫn phải được băm lại và lưu đúng nội dung mới.

        Lỗi gốc (2026-10-02): cache size+mtime tin nhầm hash cũ, nội dung mới
        không bao giờ được lưu, và restore trả về save CŨ — bản sao lưu báo
        thành công nhưng không chứa dữ liệu thật. Rất dễ gặp vì .srm có kích
        thước cố định theo loại cartridge.
        """
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        save = saves_dir / "boss.srm"

        save.write_bytes(b"AAAAAAAAAAA")          # 11 byte
        vault = SaveVault(self.primary, None, self.userdata)
        vault.create_snapshot(label="one")

        save.write_bytes(b"BBBBBBBBBBB")          # cùng 11 byte, cùng giây
        snap2 = vault.create_snapshot(label="two")
        self.assertTrue(snap2["success"])

        save.unlink()                              # xoá sạch rồi khôi phục
        vault.restore_snapshot(snap2["snapshot_id"])
        self.assertTrue(save.exists(), "restore không tạo lại file save")
        self.assertEqual(save.read_bytes(), b"BBBBBBBBBBB",
                         "snapshot phải chứa nội dung MỚI, không phải bản cũ")

    # ------------------------------------------------------------------
    # 21. Hai snapshot cùng giây + cùng label không được ghi đè lên nhau
    # ------------------------------------------------------------------
    def test_r21_same_second_snapshots_do_not_overwrite(self):
        """Hai create_snapshot cùng label trong cùng một giây phải cho hai ID
        khác nhau và cả hai đều khôi phục được.

        Lỗi gốc (2026-10-02): ID chỉ có độ phân giải giây nên trùng nhau, và
        bước commit rmtree() đè lên snapshot đã có — xoá mất cả bản pre-restore
        vừa tạo để làm đường lùi — trong khi API vẫn báo success.
        """
        saves_dir = self.primary / "Saves"
        saves_dir.mkdir(parents=True, exist_ok=True)
        save = saves_dir / "g.srm"

        save.write_bytes(b"VERSION_ONE")
        a = SaveVault(self.primary, None, self.userdata).create_snapshot(label="same")
        save.write_bytes(b"VERSION_TWO")
        vault = SaveVault(self.primary, None, self.userdata)
        b = vault.create_snapshot(label="same")

        self.assertTrue(a["success"])
        self.assertTrue(b["success"])
        self.assertNotEqual(a["snapshot_id"], b["snapshot_id"],
                            "hai snapshot khác nhau không được dùng chung một ID")

        ids = {s["id"] for s in vault.list_snapshots()}
        self.assertIn(a["snapshot_id"], ids, "snapshot cũ bị xoá mất khi tạo snapshot mới")
        self.assertIn(b["snapshot_id"], ids)

        save.unlink()
        vault.restore_snapshot(a["snapshot_id"])
        self.assertEqual(save.read_bytes(), b"VERSION_ONE",
                         "snapshot cũ phải còn khôi phục được nguyên vẹn")

    def test_r22_case_mismatch_cue_is_fixed_not_quarantined(self):
        """CUE chỉ sai hoa/thường phải được đề xuất SỬA, không được kèm đề xuất cách ly.

        Lỗi gốc (2026-10-02): hành động quarantine bị đặt ngoài nhánh else nên sinh
        cho cả cue sửa được; "Áp dụng sửa an toàn" sẽ cách ly oan file cue đó.
        """
        ps = self.primary / "Roms" / "PSX"
        ps.mkdir(parents=True, exist_ok=True)
        (ps / "Game.bin").write_bytes(b"TRACK")
        if (ps / "GAME.BIN").exists():
            self.skipTest("filesystem không phân biệt hoa/thường (Windows/NTFS) — "
                          "lỗi sai hoa/thường không tồn tại ở đây; chạy test này trên Linux")
        cue = ps / "Game.cue"
        cue.write_text('FILE "GAME.BIN" BINARY' + chr(10) + '  TRACK 01 MODE2/2352' + chr(10), encoding="utf-8")
        inv = InventoryEngine(self.primary, None).scan_all()
        rep = LibraryDoctor(self.primary, None, self.catalog, self.userdata).audit(inv, {})
        mine = [a for a in rep["fixable_actions"] if Path(a.get("path", "")).name == "Game.cue"]
        kinds = sorted(a["type"] for a in mine)
        self.assertEqual(kinds, ["fix_cue"], f"cue sai hoa/thường nhận hành động: {kinds}")


if __name__ == "__main__":
    unittest.main()
