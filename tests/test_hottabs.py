"""Leaf Hot Tabs: engine/hottabs.py + `cli hottabs ...` + màn Python.

Thẻ giả trong thư mục tạm: release.json, components.json, hottabs.manifest, jawaka-launcher giả với md5
xác định, file cờ. subprocess.run bị mock để kiểm argv (không chạy hottabs.sh thật).
Chứng minh test biết đỏ (đã làm khi viết): (1) set_resume đảo chiều on/off; (2) cho status() tạo thư mục hot-tabs/;
(3) đổi nhãn patched<->stock trong status(). Các test tương ứng phải đỏ.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.cli as CLI
from engine import autoresume as AR
from engine import hottabs as HT
from engine import ui_screens as ui

COMMIT = "6ee9d37c4d7114976870da1046f4839b10c52254"
STOCK_BIN = b"STOCK-LAUNCHER-v0.11.0"
PATCHED_BIN = b"PATCHED-LAUNCHER-v0.11.0"


def md5(b):
    return hashlib.md5(b).hexdigest()


def tree(root):
    """{đường dẫn tương đối: (loại, md5 nếu là file)} của cả cây, kể cả thư mục rỗng."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = ("f", md5(p.read_bytes())) if p.is_file() else ("d", None)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_ht_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.sd = self.tmp / "sd"
        self.ud = self.sd / ".userdata" / "mlp1"
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("USERDATA_PATH", None)

    def build(self, launcher=PATCHED_BIN, manifest=True, commit=COMMIT, have_commit=COMMIT, installer=True, release=True):
        sd = self.sd
        (sd / ".umrk" / "mlp1").mkdir(parents=True, exist_ok=True)
        if release:
            (sd / ".umrk/mlp1/release.json").write_text(json.dumps({"release_id": "v0.11.0", "version": "0.11.0"}))
            prov = sd / ".system/leaf/releases/v0.11.0/provenance"
            prov.mkdir(parents=True, exist_ok=True)
            (prov / "components.json").write_text(json.dumps({"components": [
                {"name": "leaf", "commit": "aaa"}, {"name": "launcher", "commit": have_commit}]}))
        if launcher is not None:
            b = sd / ".system/leaf/platforms/mlp1/launcher/bin"
            b.mkdir(parents=True, exist_ok=True)
            (b / "jawaka-launcher").write_bytes(launcher)
        if manifest:
            (sd / "hottabs").mkdir(exist_ok=True)
            (sd / "hottabs/hottabs.manifest").write_text(
                f"leaf_version=v0.11.0\nlauncher_commit={commit}\nvariant=launcher-only\nnames=jawaka-launcher\n"
                f"stock.loong_pangu=c61cad43b39d43b10966d296327f1c04\nstock.jawaka-launcher={md5(STOCK_BIN)}\n"
                f"patched.jawaka-launcher={md5(PATCHED_BIN)}\n")
        if installer:
            (sd / "hottabs").mkdir(exist_ok=True)
            (sd / "hottabs/hottabs.sh").write_text("#!/bin/sh\n")
        return sd

    def run_cli(self, *argv, stdin_tty=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sys.stdin, "isatty", return_value=stdin_tty):
            code = CLI.main(["--primary", str(self.sd), *argv])
        return code, out.getvalue()


class StatusTests(Base):
    def test_patched(self):
        self.build(PATCHED_BIN)
        st = HT.status(self.sd)
        self.assertEqual((st["installed"], st["bundle_matches"], st["resume_from_lists"]), ("patched", True, True))
        self.assertEqual((st["leaf_version"], st["launcher_commit"], st["bundle_version"]), ("v0.11.0", COMMIT, "v0.11.0"))
        self.assertEqual(st["installer_path"], str(self.sd / "hottabs" / "hottabs.sh"))

    def test_stock(self):
        self.build(STOCK_BIN)
        self.assertEqual(HT.status(self.sd)["installed"], "stock")

    def test_unknown_when_md5_matches_nothing(self):
        self.build(b"SOMETHING-ELSE")
        self.assertEqual(HT.status(self.sd)["installed"], "unknown")

    def test_leaf_updated_commit_mismatch(self):
        self.build(b"NEW-STOCK-OF-NEW-LEAF", have_commit="b" * 40)
        st = HT.status(self.sd)
        self.assertEqual(st["installed"], "unknown")
        self.assertFalse(st["bundle_matches"])
        self.assertIn("build lại", st["message"])

    def test_no_bundle_cannot_identify(self):
        self.build(PATCHED_BIN, manifest=False, installer=False)
        st = HT.status(self.sd)
        self.assertEqual(st["installed"], "unknown")
        self.assertIsNone(st["installer_path"])
        self.assertIsNone(st["bundle_version"])

    def test_absent_without_launcher(self):
        self.build(launcher=None)
        self.assertEqual(HT.status(self.sd)["installed"], "absent")

    def test_flag_and_backup(self):
        self.build(PATCHED_BIN)
        self.assertFalse(HT.status(self.sd)["backup_present"])
        (self.ud / "hot-tabs" / "backup" / "v0.11.0").mkdir(parents=True)
        (self.ud / "hot-tabs/backup/v0.11.0/jawaka-launcher").write_bytes(STOCK_BIN)
        (self.ud / "hot-tabs" / "no-resume-from-lists").write_bytes(b"")
        st = HT.status(self.sd)
        self.assertTrue(st["backup_present"])
        self.assertFalse(st["resume_from_lists"])

    def test_userdata_from_env_only_if_inside_card(self):
        self.build()
        elsewhere = self.tmp / "other"
        self.assertEqual(HT.userdata_for(self.sd, str(elsewhere)), self.ud)
        inside = self.sd / ".userdata" / "custom"
        self.assertEqual(HT.userdata_for(self.sd, str(inside)), inside)


class ReadOnlyTests(Base):
    def test_status_writes_nothing(self):
        self.build(PATCHED_BIN)
        before = tree(self.tmp)
        HT.status(self.sd)
        code, out = self.run_cli("--json", "hottabs", "status")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["installed"], "patched")
        self.assertEqual(tree(self.tmp), before)
        self.assertFalse((self.ud / "hot-tabs").exists())

    def test_install_without_yes_only_dry_run_and_writes_nothing(self):
        self.build(STOCK_BIN)
        before = tree(self.tmp)
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
            return mock.Mock(returncode=0, stdout=b"[dry-run] cp ...\nDry run: nothing was changed.\n")
        with mock.patch.object(HT.subprocess, "run", fake):
            for cmd in ("install", "uninstall"):
                code, out = self.run_cli("--json", "hottabs", cmd)
                self.assertEqual(code, 1)
                j = json.loads(out)
                self.assertFalse(j["success"])
                self.assertTrue(j["preview"])
                self.assertIn("Dry run", j["output"])
        self.assertEqual(len(calls), 2)
        for argv, _ in calls:
            self.assertIn("--dry-run", argv)
        self.assertEqual(tree(self.tmp), before)


class ToggleTests(Base):
    def test_off_creates_flag_on_removes_it(self):
        self.build()
        flag = self.ud / "hot-tabs" / "no-resume-from-lists"
        code, out = self.run_cli("--json", "hottabs", "off")
        self.assertEqual(code, 0)
        self.assertTrue(flag.is_file())
        self.assertEqual(flag.read_bytes(), b"")
        self.assertEqual(json.loads(out)["resume_from_lists"], False)
        self.assertFalse(json.loads(self.run_cli("--json", "hottabs", "status")[1])["resume_from_lists"])
        self.assertEqual([p.name for p in flag.parent.iterdir()], ["no-resume-from-lists"])   # không để file tạm
        code, out = self.run_cli("--json", "hottabs", "on")
        self.assertEqual(code, 0)
        self.assertFalse(flag.exists())
        self.assertTrue(json.loads(out)["resume_from_lists"])
        self.assertTrue(json.loads(self.run_cli("--json", "hottabs", "status")[1])["resume_from_lists"])

    def test_idempotent(self):
        self.build()
        self.assertTrue(HT.set_resume(self.ud, True)["changed"] is False)      # đã bật, bật lại: không đổi gì
        self.assertTrue(HT.set_resume(self.ud, False)["changed"])
        self.assertFalse(HT.set_resume(self.ud, False)["changed"])
        self.assertTrue(HT.set_resume(self.ud, True)["changed"])

    def test_on_does_not_touch_backup_or_other_files(self):
        self.build()
        keep = self.ud / "hot-tabs" / "backup" / "v0.11.0" / "jawaka-launcher"
        keep.parent.mkdir(parents=True)
        keep.write_bytes(STOCK_BIN)
        HT.set_resume(self.ud, False)
        HT.set_resume(self.ud, True)
        self.assertEqual(keep.read_bytes(), STOCK_BIN)


class InstallerTests(Base):
    def test_argv_fixed_no_shell(self):
        self.build(STOCK_BIN)
        calls = []

        def fake(argv, **kw):
            calls.append((argv, kw))
            return mock.Mock(returncode=0, stdout=b"Installed. Reboot the device\n")
        sd = str(self.sd)
        script = str(self.sd / "hottabs" / "hottabs.sh")
        with mock.patch.object(HT.subprocess, "run", fake):
            code, out = self.run_cli("--json", "hottabs", "install", "--yes")
            j = json.loads(out)
            self.assertEqual(code, 0)
            self.assertTrue(j["success"] and j["reboot_required"])
            code, out = self.run_cli("--json", "hottabs", "uninstall", "--yes")
            self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in calls], [
            ["sh", script, "--sd", sd, "--dry-run", "install"], ["sh", script, "--sd", sd, "install"],
            ["sh", script, "--sd", sd, "--dry-run", "uninstall"], ["sh", script, "--sd", sd, "uninstall"]])
        for argv, kw in calls:
            self.assertIsInstance(argv, list)
            self.assertFalse(kw.get("shell"))

    def test_hostile_path_stays_one_argv_element(self):
        hostile = self.tmp / "sd; touch pwned #"
        self.sd = hostile
        self.build(STOCK_BIN)
        argv = HT.installer_argv(hostile, "install", False)
        self.assertEqual(argv, ["sh", str(hostile / "hottabs" / "hottabs.sh"), "--sd", str(hostile), "install"])

    def test_installer_refusal_does_not_run_real_command(self):
        self.build(STOCK_BIN)
        calls = []

        def fake(argv, **kw):
            calls.append(argv)
            return mock.Mock(returncode=1, stdout=b"ERROR: this bundle is for Jawaka X\n")
        with mock.patch.object(HT.subprocess, "run", fake):
            code, out = self.run_cli("--json", "hottabs", "install", "--yes")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["success"])
        self.assertEqual(len(calls), 1)
        self.assertIn("--dry-run", calls[0])

    def test_no_bundle_is_usage_error(self):
        self.build(STOCK_BIN, manifest=False, installer=False)
        code, out = self.run_cli("--json", "hottabs", "install", "--yes")
        self.assertEqual(code, 2)

    def test_unknown_action_rejected(self):
        self.build()
        with self.assertRaises(ValueError):
            HT.installer_argv(self.sd, "rm -rf /", False)


class UiTests(Base):
    def app(self):
        app = mock.Mock()
        app.probe.primary_path = self.sd
        app.app_data = self.ud / "LoongMate"
        app.app_data.mkdir(parents=True, exist_ok=True)
        return app

    def rows(self, app, compat_ok=None):
        fake = {"ok": compat_ok, "message": "x", "problems": [], "notes": []}
        with mock.patch.object(ui.autoresume_mod, "compat", return_value=fake):
            header, items = ui.App._build_hottabs(app)
        return header, {i.label: i.tag for i in items if not getattr(i, "head", False)}

    def test_ui_builder_labels(self):
        """Màn Python dựng đúng theo trạng thái thật (không cần SDL)."""
        self.build(PATCHED_BIN)
        app = self.app()
        header, rows = self.rows(app)
        self.assertIn("Hot Tabs", "\n".join(header))
        self.assertEqual(rows["Qua Hot Tabs"], "Bật")
        HT.set_resume(self.ud, False)
        _, rows = self.rows(app)
        self.assertEqual(rows["Qua Hot Tabs"], "Tắt")

    def test_without_hot_tabs_only_the_agent_row_is_shown(self):
        """Bản phát hành công khai không kèm Hot Tabs: màn không được hiện dòng Hot Tabs 'Bật' gây hiểu nhầm."""
        self.build(STOCK_BIN, manifest=False, installer=False)
        app = self.app()
        header, rows = self.rows(app)
        self.assertEqual(list(rows), ["Qua Agent", "Kiểm với Leaf này"])
        self.assertEqual(rows["Qua Agent"], "Tắt")
        self.assertEqual(rows["Kiểm với Leaf này"], "chưa rõ")
        self.assertIn("TẮT", header[0])

    def test_enabled_without_running_agent_warns(self):
        self.build(STOCK_BIN, manifest=False, installer=False)
        app = self.app()
        AR.set_enabled(app.app_data, True)
        header, rows = self.rows(app)
        self.assertEqual(rows["Qua Agent"], "Bật")
        self.assertIn("chưa chạy", header[0])

    def test_leaf_structure_change_surfaces_hot_tabs_fallback(self):
        """Chỉ khi Leaf đổi cấu trúc (Agent không chạy được) mới đưa Hot Tabs ra làm phương án vá."""
        self.build(STOCK_BIN, manifest=False, installer=False)
        app = self.app()
        AR.set_enabled(app.app_data, True)
        header, rows = self.rows(app, compat_ok=False)
        self.assertIn("đổi cấu trúc", header[0])
        self.assertEqual(rows["Kiểm với Leaf này"], "lệch")
        self.assertIn("Cài lại Hot Tabs", rows)
        _, rows = self.rows(app, compat_ok=True)
        self.assertEqual(rows["Kiểm với Leaf này"], "tương thích")
        self.assertNotIn("Cài lại Hot Tabs", rows)

    def test_cli_status_reports_agent_state(self):
        self.build(STOCK_BIN, manifest=False, installer=False)
        code, out = self.run_cli("--json", "hottabs", "status")
        self.assertEqual(code, 0)
        st = json.loads(out)
        self.assertIn("agent_running", st)
        self.assertFalse(st["agent_running"])


if __name__ == "__main__":
    unittest.main()
