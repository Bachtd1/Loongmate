"""
Gộp hai app (LoongMate + LoongMate Native) thành một.

  1. launch.sh: chạy bằng `sh` thật với bin/lm-home và python GIẢ — native thoát 0 thì không mở Python; native chết
     ngay (127 / signal / mã != 0 trong vài giây đầu) hoặc không có bin hoặc LOONGMATE_UI=python thì mở Python và ghi lý do.
  2. update.py: manifest "remove_paks" — gỡ đúng, lỗi giữa chừng thì pak bị gỡ còn nguyên, rollback trả lại, verify từ chối tên lạ/trùng.
  3. pc/deploy.py: gói gồm LoongMate.pak (có bin/lm-home) + LoongMateAgent.pak, manifest có remove_paks, rescan chỉ cảnh báo.

Mọi test chạy hành vi thật (thư mục tạm); không grep mã nguồn.
Chứng minh biết đỏ (đã làm tay, xem báo cáo): bỏ nhánh `_undo` của pak bị gỡ trong update.py thì test_21/test_22 đỏ;
bỏ `missing` trong rollback thì test_31 đỏ; đổi ngưỡng/điều kiện rơi-về trong launch.sh thì các test launch tương ứng đỏ.
"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import test_pc_wave_c as pcw  # noqa: E402  (up = update.py đang thử, dp = deploy.py; helper dựng cây/gói)

up, dp = pcw.up, pcw.dp
write_tree, tree, sha = pcw.write_tree, pcw.tree, pcw.sha

LAUNCH_SRC = ROOT / "pak" / "Apps" / "mlp1" / "LoongMate.pak" / "launch.sh"
SH = shutil.which("sh")


# --------------------------------------------------------------------------- 1. launch.sh

class TestLaunchFiles(unittest.TestCase):
    def test_00_launch_sh_is_lf_and_parses(self):
        data = LAUNCH_SRC.read_bytes()
        self.assertNotIn(b"\r", data)
        if SH:
            p = subprocess.run([SH, "-n", str(LAUNCH_SRC)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))

    def test_01_repo_has_one_pak_with_only_lm_home_in_bin(self):
        apps = ROOT / "pak" / "Apps" / "mlp1"
        self.assertFalse((apps / "LoongMateNative.pak").exists(), "LoongMateNative.pak phải bị xoá khỏi repo")
        self.assertEqual(sorted(p.name for p in (apps / "LoongMate.pak" / "bin").iterdir()), ["lm-home"])
        self.assertGreater((apps / "LoongMate.pak" / "bin" / "lm-home").stat().st_size, 0)


@unittest.skipUnless(SH, "cần sh (Linux/Git Bash); CI Linux chạy")
class TestLaunchBehaviour(unittest.TestCase):
    """launch.sh thật, bin/lm-home giả (script sh), python giả ghi lại lời gọi."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.pak = self.root / "pak"
        (self.pak / "bin").mkdir(parents=True)
        (self.pak / "engine").mkdir()
        shutil.copyfile(LAUNCH_SRC, self.pak / "launch.sh")
        self.marker = self.root / "marker"
        self.fakepy = self.root / "fakepy"
        self._script(self.fakepy, f'echo "PYTHON $*" >> "{self.marker.as_posix()}"\nexit 0\n')
        self.ud = self.root / "ud"

    def _script(self, path, body):
        path.write_bytes(("#!/bin/sh\n" + body).encode("utf-8"))
        path.chmod(0o755)

    def native(self, body):
        self._script(self.pak / "bin" / "lm-home", f'echo "NATIVE $*" >> "{self.marker.as_posix()}"\n' + body)

    def run_launch(self, extra_env=None, args=()):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LOONGMATE_")}
        env.update({"SDCARD_PATH": (self.root / "sd").as_posix(), "USERDATA_PATH": self.ud.as_posix(),
                    "LOONGMATE_PYTHON": self.fakepy.as_posix()})
        env.update(extra_env or {})
        p = subprocess.run([SH, (self.pak / "launch.sh").as_posix(), *args], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        out = p.stdout.decode("utf-8", "replace")
        logs = sorted((self.ud / "LoongMate" / "logs").glob("loongmate_*.log"))
        log = "\n".join(l.read_text(encoding="utf-8") for l in logs)
        calls = self.marker.read_text(encoding="utf-8").splitlines() if self.marker.exists() else []
        return p.returncode, out, log, calls

    def test_10_native_exit_0_does_not_open_python(self):
        self.native("exit 0\n")
        rc, out, log, calls = self.run_launch()
        self.assertEqual(rc, 0, out)
        self.assertEqual([c.split()[0] for c in calls], ["NATIVE"], calls)
        self.assertRegex(calls[0], r"--pak \S*/pak --userdata \S*/ud/LoongMate")   # --pak là CHÍNH pak này (engine nằm cùng pak)
        self.assertIn("exited with code 0", log)

    def test_11_native_exit_127_falls_back_to_python_and_logs_reason(self):
        self.native("exit 127\n")
        rc, out, log, calls = self.run_launch()
        self.assertEqual([c.split()[0] for c in calls], ["NATIVE", "PYTHON"], calls)
        self.assertIn("ui_sdl.py", calls[1])
        self.assertIn("127", log)
        self.assertIn("chuyển sang giao diện Python", log)
        self.assertEqual(rc, 0)

    def test_12_native_killed_by_segv_falls_back_to_python(self):
        self.native("kill -SEGV $$\n")
        rc, out, log, calls = self.run_launch()
        self.assertEqual([c.split()[0] for c in calls], ["NATIVE", "PYTHON"], calls)
        self.assertIn("signal 11", log)

    def test_13_native_quick_nonzero_exit_falls_back_to_python(self):
        self.native("exit 3\n")
        rc, out, log, calls = self.run_launch()
        self.assertEqual([c.split()[0] for c in calls], ["NATIVE", "PYTHON"], calls)
        self.assertIn("thoát mã 3 ngay khi khởi động", log)

    def test_14_native_late_nonzero_exit_does_not_open_python(self):
        """Đã chạy hơn ngưỡng ~3 s thì lỗi là lỗi giữa phiên: không bật Python bất ngờ, trả đúng mã của native."""
        self.native("sleep 5\nexit 3\n")
        rc, out, log, calls = self.run_launch()
        self.assertEqual([c.split()[0] for c in calls], ["NATIVE"], calls)
        self.assertEqual(rc, 3)
        self.assertIn("KHÔNG tự mở Python", log)

    def test_15_no_native_binary_uses_python(self):
        rc, out, log, calls = self.run_launch()
        self.assertEqual([c.split()[0] for c in calls], ["PYTHON"], calls)
        self.assertIn("Không có giao diện native", log)
        self.assertEqual(rc, 0)

    def test_16_loongmate_ui_python_forces_python(self):
        self.native("exit 0\n")
        rc, out, log, calls = self.run_launch({"LOONGMATE_UI": "python"})
        self.assertEqual([c.split()[0] for c in calls], ["PYTHON"], calls)
        self.assertIn("LOONGMATE_UI=python", log)

    def test_17_python_failure_after_fallback_still_reports_its_exit(self):
        """Native chết + Python UI cũng thoát != 0 -> vẫn rơi tiếp về nhánh dự phòng cũ (không âm thầm báo 0 giả)."""
        self.native("exit 127\n")
        self._script(self.fakepy, f'echo "PYTHON $*" >> "{self.marker.as_posix()}"\nexit 4\n')
        rc, out, log, calls = self.run_launch()
        self.assertEqual(calls[0].split()[0], "NATIVE")
        self.assertIn("PYTHON", calls[1])
        self.assertIn("Giao diện SDL thoát với mã 4", log)


# --------------------------------------------------------------------------- 2. update.py remove_paks

V_OLD = {"LoongMate.pak/launch.sh": b"#!/bin/sh\nold\n", "LoongMate.pak/engine/cli.py": b"print('old')\n",
         "LoongMateAgent.pak/launch.sh": b"#!/bin/sh\nagent-old\n",
         "LoongMateNative.pak/launch.sh": b"#!/bin/sh\nnative-old\n", "LoongMateNative.pak/bin/lm-home": b"NATIVE-OLD",
         "LoongMateNative.pak/res/icon.png": b"PNG"}
V_NEW = {"LoongMate.pak/launch.sh": b"#!/bin/sh\nnew\n", "LoongMate.pak/engine/cli.py": b"print('new')\n",
         "LoongMate.pak/bin/lm-home": b"NATIVE-NEW",
         "LoongMateAgent.pak/launch.sh": b"#!/bin/sh\nagent-new\n"}
REMOVE = {"remove_paks": ["LoongMateNative.pak"]}


class RemoveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.apps, self.data, self.bundle = root / "Apps", root / "ud" / "LoongMate", root / "bundle"
        write_tree(self.apps, V_OLD)
        write_tree(self.apps, {"Other.pak/launch.sh": b"other"})
        write_tree(root, {"Roms/GB/g.gb": b"ROM", "Saves/GB/g.srm": b"SAVE"})
        self.outside = {p: tree(root / p) for p in ("Roms", "Saves")}
        self.root = root
        self.before = tree(self.apps)
        pcw.make_bundle(self.bundle, V_NEW, version="v3", extra_manifest=REMOVE)

    def leftovers(self):
        return [p.name for p in self.apps.iterdir() if p.name.startswith(".")]

    def assert_untouched_outside(self):
        for p, b in self.outside.items():
            self.assertEqual(tree(self.root / p), b)
        self.assertEqual(tree(self.apps / "Other.pak"), {"launch.sh": b"other"})


class TestRemovePaksApply(RemoveBase):
    def test_10_apply_removes_the_pak_and_backs_it_up(self):
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["removed_paks"], ["LoongMateNative.pak"])
        self.assertFalse((self.apps / "LoongMateNative.pak").exists())
        self.assertEqual(self.leftovers(), [])
        self.assertEqual((self.apps / "LoongMate.pak/bin/lm-home").read_bytes(), b"NATIVE-NEW")
        self.assertEqual((self.apps / "LoongMateAgent.pak/launch.sh").read_bytes(), b"#!/bin/sh\nagent-new\n")
        self.assert_untouched_outside()
        bk = self.data / "pak-backup" / r["backup"]
        self.assertEqual(tree(bk / "LoongMateNative.pak"),
                         {k.split("/", 1)[1]: v for k, v in V_OLD.items() if k.startswith("LoongMateNative.pak/")})
        meta = json.loads((bk / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["removed_paks"], ["LoongMateNative.pak"])
        self.assertIn("LoongMateNative.pak", meta["paks"])
        iv = json.loads((self.data / "installed-version.json").read_text(encoding="utf-8"))
        self.assertEqual(iv["removed_paks"], ["LoongMateNative.pak"])

    def test_11_pak_already_gone_is_not_an_error(self):
        shutil.rmtree(self.apps / "LoongMateNative.pak")
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["removed_paks"], [])

    def test_12_bundle_without_remove_paks_leaves_other_paks_alone(self):
        shutil.rmtree(self.bundle)
        pcw.make_bundle(self.bundle, V_NEW, version="v3")
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["removed_paks"], [])
        self.assertEqual((self.apps / "LoongMateNative.pak/bin/lm-home").read_bytes(), b"NATIVE-OLD")

    def test_20_removal_failure_mid_way_restores_everything_including_removed_pak(self):
        real = up._remove_one

        def half(apps_dir, name):
            real(apps_dir, name)             # đã đổi tên sang .old rồi mới hỏng: phải được đưa về
            raise OSError("đĩa đầy giả lập")

        with mock.patch.object(up, "_remove_one", side_effect=half):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(tree(self.apps), self.before)          # CẢ 4 pak về đúng như cũ, kể cả pak bị gỡ
        self.assertEqual(self.leftovers(), [])
        self.assertFalse((self.data / "pak-backup").exists() and any((self.data / "pak-backup").iterdir()))
        self.assert_untouched_outside()

    def test_21_failure_after_removal_restores_removed_pak(self):
        """Pak bị gỡ đã đổi tên sang .old, rồi bước ghi installed-version.json hỏng -> phải đưa nó về."""
        real = up._write_json_atomic

        def boom(path, obj):
            if Path(path).name == "installed-version.json":
                raise OSError("không ghi được")
            return real(path, obj)

        with mock.patch.object(up, "_write_json_atomic", side_effect=boom):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(tree(self.apps), self.before)
        self.assertEqual(self.leftovers(), [])

    def test_22_failure_swapping_a_normal_pak_leaves_removed_pak_untouched(self):
        calls = []
        real = up._swap_one

        def flaky(apps_dir, name):
            calls.append(name)
            if name == "LoongMateAgent.pak":
                raise OSError("rename hỏng")
            return real(apps_dir, name)

        with mock.patch.object(up, "_swap_one", side_effect=flaky):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(tree(self.apps), self.before)
        self.assertEqual(self.leftovers(), [])


class TestRemovePaksRollback(RemoveBase):
    def test_30_rollback_brings_back_the_removed_pak(self):
        self.assertTrue(up.apply_update(self.bundle, self.apps, self.data)["ok"])
        self.assertFalse((self.apps / "LoongMateNative.pak").exists())
        r = up.rollback(self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["restored_removed_paks"], ["LoongMateNative.pak"])
        self.assertEqual(tree(self.apps), self.before)           # đủ 3 pak + Other như trước, khớp từng byte
        self.assertEqual(self.leftovers(), [])
        self.assert_untouched_outside()

    def test_31_rollback_failure_leaves_the_pak_still_removed(self):
        """Rollback hỏng ở bước cuối thì không được để lại pak vừa chép lại một nửa: trả về đúng trạng thái sau cập nhật."""
        self.assertTrue(up.apply_update(self.bundle, self.apps, self.data)["ok"])
        after = tree(self.apps)
        real = up._write_json_atomic

        def boom(path, obj):
            if Path(path).name == "installed-version.json":
                raise OSError("không ghi được")
            return real(path, obj)

        with mock.patch.object(up, "_write_json_atomic", side_effect=boom):
            r = up.rollback(self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(tree(self.apps), after)
        self.assertEqual(self.leftovers(), [])


class TestRemovePaksVerify(RemoveBase):
    def verify_with(self, remove):
        shutil.rmtree(self.bundle)
        pcw.make_bundle(self.bundle, V_NEW, version="v3", extra_manifest={"remove_paks": remove})
        return up.verify_bundle(self.bundle)

    def test_40_good(self):
        r = up.verify_bundle(self.bundle)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["remove_paks"], ["LoongMateNative.pak"])

    def test_41_unknown_name_refused_and_apply_changes_nothing(self):
        for bad in ("Other.pak", "../Apps", "LoongMateNative.pak/", ""):
            r = self.verify_with([bad])
            self.assertFalse(r["ok"], bad)
            self.assertTrue(any("remove_paks" in e for e in r["errors"]), r["errors"])
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "verify")
        self.assertEqual(tree(self.apps), self.before)

    def test_42_name_that_is_also_in_bundle_refused(self):
        r = self.verify_with(["LoongMateAgent.pak"])
        self.assertFalse(r["ok"])
        self.assertTrue(any("vừa nằm trong gói" in e for e in r["errors"]), r["errors"])

    def test_43_removing_the_running_engine_pak_refused(self):
        shutil.rmtree(self.bundle)
        files = {k: v for k, v in V_NEW.items() if not k.startswith("LoongMate.pak/")}
        pcw.make_bundle(self.bundle, files, version="v3", extra_manifest={"remove_paks": ["LoongMate.pak"]})
        r = up.verify_bundle(self.bundle)
        self.assertFalse(r["ok"])

    def test_44_malformed_type_refused(self):
        for bad in ("LoongMateNative.pak", {"a": 1}, [1], None):
            r = self.verify_with(bad)
            self.assertFalse(r["ok"], bad)


# --------------------------------------------------------------------------- 3. pc/deploy.py

class TestDeployBundle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        files = {"pak/build/LoongMate.pak/launch.sh": b"#!/bin/sh\nlaunch\n",
                 "pak/build/LoongMate.pak/bin/lm-home": b"NATIVE",
                 "pak/build/LoongMate.pak/engine/cli.py": b"print(1)\n",
                 "pak/build/LoongMate.pak/engine/update.py": (ROOT / "engine" / "update.py").read_bytes(),
                 "pak/Apps/mlp1/LoongMateAgent.pak/launch.sh": b"agent",
                 # nếu ai đó lỡ để lại pak cũ trong repo thì deploy KHÔNG được đóng gói nó:
                 "pak/Apps/mlp1/LoongMateNative.pak/bin/lm-home": b"STALE"}
        write_tree(self.repo, files)
        (self.repo / ".gitignore").write_text("pak/build/\n")
        pcw.run_git(self.repo, "init", "-q")
        pcw.run_git(self.repo, "add", "-A")
        pcw.run_git(self.repo, "commit", "-q", "-m", "init")

    def test_50_bundle_has_exactly_loongmate_and_agent_plus_remove_paks(self):
        out = Path(self._tmp.name) / "bundle"
        m = dp.make_bundle(dp.sources(self.repo), out, "abc1234", dp.REMOVE_PAKS)
        self.assertEqual(sorted({k.split("/")[0] for k in m["files"]}), ["LoongMate.pak", "LoongMateAgent.pak"])
        self.assertIn("LoongMate.pak/bin/lm-home", m["files"])
        self.assertEqual(m["remove_paks"], ["LoongMateNative.pak"])
        v = dp.load_update_module(out).verify_bundle(out)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["remove_paks"], ["LoongMateNative.pak"])

    def test_51_bundle_applies_and_removes_old_native_pak_with_bundled_update_py(self):
        out = Path(self._tmp.name) / "bundle"
        dp.make_bundle(dp.sources(self.repo), out, "abc1234", dp.REMOVE_PAKS)
        mod = dp.load_update_module(out)
        apps, data = Path(self._tmp.name) / "Apps", Path(self._tmp.name) / "data"
        write_tree(apps, {"LoongMate.pak/launch.sh": b"old", "LoongMateNative.pak/bin/lm-home": b"old-native"})
        r = mod.apply_update(out, apps, data)
        self.assertTrue(r["ok"], r)
        self.assertFalse((apps / "LoongMateNative.pak").exists())
        self.assertEqual((apps / "LoongMate.pak/bin/lm-home").read_bytes(), b"NATIVE")

    def test_52_dry_run_refuses_bundle_without_native_binary(self):
        (self.repo / "pak/build/LoongMate.pak/bin/lm-home").unlink()
        # pak/build bị .gitignore nên working tree vẫn sạch
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = dp.main(["--dry-run", "--offline", "--skip-build", "--root", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("bin/lm-home", err.getvalue())

    def test_53_dry_run_ok_with_native_binary(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dp.main(["--dry-run", "--offline", "--skip-build", "--root", str(self.repo)])
        self.assertEqual(rc, 0, out.getvalue())


class FakeRemote:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.cmds = result, exc, []

    def run_json(self, cmd, timeout=600):
        self.cmds.append(cmd)
        if self.exc:
            raise self.exc
        return self.result


class Args:
    apps_dir = "/mnt/sdcard/Apps/mlp1"
    app_data = "/mnt/sdcard/.userdata/mlp1/LoongMate"
    device_python = "/userdata/loongmate-runtime/python/bin/python3"


class TestDeployRescan(unittest.TestCase):
    def test_60_rescan_calls_installed_cli(self):
        rem = FakeRemote(({"ok": True, "message": "Quét xong"}, 0))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(dp.run_rescan(rem, Args))
        self.assertIn("/mnt/sdcard/Apps/mlp1/LoongMate.pak/engine/cli.py", rem.cmds[0])
        self.assertIn("rescan", rem.cmds[0])

    def test_61_rescan_failures_only_warn(self):
        for rem in (FakeRemote(({"ok": False, "message": "jawakad không chạy"}, 1)),
                    FakeRemote(exc=dp.DeployError("không in JSON")),
                    FakeRemote(exc=dp.pull_saves.Unreachable("mất kết nối"))):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertFalse(dp.run_rescan(rem, Args))
            self.assertIn("CẢNH BÁO", out.getvalue())


if __name__ == "__main__":
    unittest.main()
