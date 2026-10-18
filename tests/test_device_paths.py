"""
Tests cho pc/device_paths.py: dò thẻ Leaf trên máy thay vì ghi cứng /mnt/sdcard.

"Máy giả" là THƯ MỤC TẠM có bố cục thẻ thật; script dò chạy THẬT bằng `sh` cục bộ (thay cho ssh),
nên test đi qua đúng đoạn sh gửi sang máy (pwd -P, nạp env.sh trong subshell...). env.sh giả bắt
chước hợp đồng của Leaf: "giá trị có sẵn thắng, chỉ điền chỗ trống" và mặc định SDCARD_PATH=/mnt/sdcard.
Chứng minh đỏ: LOONGMATE_PULL_PATH / LOONGMATE_DEPLOY_PATH / LOONGMATE_DEVICE_PATHS_PATH trỏ tới bản cố tình hỏng.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_pull_saves as tps  # noqa: E402
import test_pc_wave_c as pcw  # noqa: E402

ps, dp, rm = tps.ps, pcw.dp, pcw.rm

_ov = os.environ.get("LOONGMATE_DEVICE_PATHS_PATH")
if _ov:
    _spec = importlib.util.spec_from_file_location("device_paths", _ov)
    dev = importlib.util.module_from_spec(_spec)
    sys.modules["device_paths"] = dev
    _spec.loader.exec_module(dev)
else:
    dev = sys.modules.get("device_paths") or importlib.import_module("device_paths")

SH = shutil.which("sh")
ENV_REL = ".system/leaf/platforms/mlp1/launcher/env.sh"
# Bắt chước env.sh của Leaf: mặc định /mnt/sdcard, giá trị có sẵn thắng.
FAKE_ENV = ': "${SDCARD_PATH:=/mnt/sdcard}"\n: "${USERDATA_PATH:=$SDCARD_PATH/.userdata/mlp1}"\nexport SDCARD_PATH USERDATA_PATH\n'


def posix(p):
    p = Path(p).resolve()
    s = p.as_posix()
    if os.name == "nt" and p.drive:
        s = "/" + p.drive[0].lower() + s[2:]
    return s


def make_card(root, leaf):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if leaf:
        e = root / ENV_REL
        e.parent.mkdir(parents=True, exist_ok=True)
        e.write_text(FAKE_ENV, encoding="utf-8", newline="\n")
    return root


class ShellSsh(ps.SshTransport):
    """SshTransport thật, chỉ thay "ssh user@host <lệnh>" bằng "sh -c <lệnh>" chạy cục bộ."""

    def _cmd(self, remote):
        return [SH, "-c", remote]


@unittest.skipUnless(SH, "cần `sh` (Git Bash/WSL) để chạy script dò")
class DetectBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.a = self.tmp / "mnt_sdcard"      # "/mnt/sdcard"
        self.b = self.tmp / "media_sdcard1"   # "/media/sdcard1"
        cands = (posix(self.a), posix(self.b))
        p = mock.patch.object(dev, "CANDIDATES", cands)
        p.start()
        self.addCleanup(p.stop)
        self.cands = cands

    def transport(self, **kw):
        return ShellSsh(**kw)


class TestDetect(DetectBase):
    def test_leaf_on_first_slot(self):
        make_card(self.a, True)
        make_card(self.b, False)
        lp = dev.get(self.transport())
        self.assertEqual(lp.sdcard, posix(self.a))
        self.assertEqual(lp.userdata, posix(self.a) + "/.userdata/mlp1")

    def test_leaf_swapped_to_second_slot(self):
        """Đúng sự cố 2026-10-03: thẻ Leaf ở /media/sdcard1, thẻ kia ở /mnt/sdcard KHÔNG có Leaf."""
        make_card(self.a, False)
        make_card(self.b, True)
        lp = dev.get(self.transport())
        self.assertEqual(lp.sdcard, posix(self.b))
        # bẫy của env.sh: không gán SDCARD_PATH trước thì nó mặc định /mnt/sdcard
        self.assertEqual(lp.vault, posix(self.b) + "/.userdata/mlp1/LoongMate/SaveVault")
        self.assertEqual(lp.apps_dir, posix(self.b) + "/Apps/mlp1")
        self.assertEqual(lp.app_data, posix(self.b) + "/.userdata/mlp1/LoongMate")
        self.assertEqual(lp.remote_dir, posix(self.b) + "/LoongMate-update")

    def test_no_leaf_card_is_clear_error(self):
        make_card(self.a, False)
        make_card(self.b, False)
        with self.assertRaises(dev.LeafCardNotFound) as cm:
            dev.get(self.transport())
        self.assertIn("Không thấy thẻ nào chứa Leaf", str(cm.exception))

    def test_missing_dirs_is_not_found(self):
        with self.assertRaises(dev.LeafCardNotFound):
            dev.get(self.transport())

    def test_two_leaf_cards_is_ambiguous(self):
        make_card(self.a, True)
        make_card(self.b, True)
        with self.assertRaises(dev.LeafCardAmbiguous) as cm:
            dev.get(self.transport())
        self.assertIn("--sdcard", str(cm.exception))

    def test_sdcard_flag_resolves_ambiguity(self):
        make_card(self.a, True)
        make_card(self.b, True)
        lp = dev.get(self.transport(), sdcard=posix(self.b))
        self.assertEqual(lp.sdcard, posix(self.b))

    def test_same_card_listed_twice_is_not_ambiguous(self):
        """Hai đường dẫn cùng trỏ một thẻ (như /media/sdcard0 -> /mnt/sdcard) chỉ tính một."""
        make_card(self.a, True)
        alias = posix(self.a) + "/../" + self.a.name
        with mock.patch.object(dev, "CANDIDATES", (posix(self.a), alias)):
            self.assertEqual(dev.get(self.transport()).sdcard, posix(self.a))

    def test_real_symlink_alias_is_not_ambiguous(self):
        make_card(self.a, True)
        link = self.tmp / "sdcard0"
        try:
            os.symlink(self.a, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("không tạo được symlink trên môi trường này")
        with mock.patch.object(dev, "CANDIDATES", (posix(link), posix(self.a))):
            self.assertEqual(dev.get(self.transport()).sdcard, posix(self.a))

    def test_cached_within_one_run_only(self):
        make_card(self.a, True)
        t = self.transport()
        first = dev.get(t)
        shutil.rmtree(self.a)           # đổi bố cục giữa chừng: cùng transport vẫn trả bản đã dò
        make_card(self.b, True)
        self.assertEqual(dev.get(t), first)
        # lần chạy mới (transport mới) phải dò lại, không nhớ gì
        self.assertEqual(dev.get(self.transport()).sdcard, posix(self.b))

    def test_hostile_env_output_rejected(self):
        with self.assertRaises(dev.LeafCardError):
            dev.parse(b"LEAF\t/mnt/sd card'; rm -rf /\t/mnt/sd/.userdata\n")
        with self.assertRaises(dev.LeafCardError):
            dev.parse(b"LEAF\t/mnt/sdcard\t/elsewhere/.userdata\n")


class TestPullUsesDetectedPaths(DetectBase):
    def setUp(self):
        super().setUp()
        make_card(self.a, False)
        make_card(self.b, True)
        self.vault = self.b / ".userdata" / "mlp1" / "LoongMate" / "SaveVault"
        self.data = b"SAVE-DATA-1"
        tps.make_snapshot(self.vault, "snap_1", {"Saves/a.srm": self.data})
        self.dest = self.tmp / "pc" / "SaveVault"
        self.log = str(self.tmp / "pull.log")
        p = mock.patch.object(ps, "SshTransport", ShellSsh)   # main() tự dựng transport từ cờ, như khi chạy thật
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: [(h.close(), ps.log.removeHandler(h)) for h in list(ps.log.handlers)])

    def pull(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ps.main(["--dest", str(self.dest), "--log", self.log, *args])
        return rc, out.getvalue()

    def test_pull_from_swapped_card_and_offsite_on_same_card(self):
        rc, out = self.pull()
        self.assertEqual(rc, 0, out)
        self.assertIn("Thẻ Leaf: " + posix(self.b), out)
        self.assertEqual(len(list((self.dest / "snapshots").iterdir())), 1)
        off = self.vault.parent / "offsite.json"
        self.assertEqual(json.loads(off.read_text("utf-8"))["snapshot_ids"], ["snap_1"])
        self.assertFalse((self.a / ".userdata").exists(), "không được ghi gì vào thẻ không phải Leaf")

    def test_explicit_device_vault_wins_over_detection(self):
        other = self.tmp / "custom" / "SaveVault"
        tps.make_snapshot(other, "snap_custom", {"Saves/z.srm": b"Z"})
        rc, out = self.pull("--device-vault", posix(other))
        self.assertEqual(rc, 0, out)
        self.assertEqual([p.name for p in (self.dest / "snapshots").iterdir()], ["snap_custom"])
        self.assertNotIn("Thẻ Leaf", out)

    def test_no_leaf_card_exit_1_ambiguous_exit_3(self):
        shutil.rmtree(self.b)
        rc, out = self.pull()
        self.assertEqual(rc, 1)
        self.assertIn("Không thấy thẻ nào chứa Leaf", out)
        make_card(self.a, True)
        make_card(self.b, True)
        rc, out = self.pull()
        self.assertEqual(rc, 3)
        self.assertFalse(self.dest.exists(), "lỗi dò thẻ thì không tạo thư mục trên PC")

    def test_restore_menu_list_and_push_use_detected_vault(self):
        self.assertEqual(self.pull()[0], 0)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = rm.main(["--dest", str(self.dest), "--list", "--json"])
        self.assertEqual(rc, 0, err.getvalue())
        j = json.loads(out.getvalue())
        self.assertEqual(j["device_vault"], posix(self.vault))
        self.assertTrue(j["snapshots"][0]["on_device"])
        # đẩy ngược một snapshot mới chỉ có trên PC: phải rơi vào vault của thẻ Leaf
        tps.make_snapshot(self.dest, "snap_pc", {"Saves/p.srm": b"PPP"})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = rm.main(["--dest", str(self.dest), "--push", "snap_pc", "--yes", "--json"])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertTrue((self.vault / "snapshots" / "snap_pc" / "manifest.json").is_file())
        self.assertFalse((self.a / ".userdata").exists())


class TestDeployTargets(DetectBase):
    def args(self, **kw):
        d = dict(apps_dir=None, app_data=None, remote_dir=None, sdcard=None)
        d.update(kw)
        return Namespace(**d)

    def test_defaults_from_detected_card(self):
        make_card(self.a, False)
        make_card(self.b, True)
        a = self.args()
        with contextlib.redirect_stdout(io.StringIO()):
            dp.resolve_targets(self.transport(), a)
        b = posix(self.b)
        self.assertEqual((a.apps_dir, a.app_data, a.remote_dir),
                         (b + "/Apps/mlp1", b + "/.userdata/mlp1/LoongMate", b + "/LoongMate-update"))

    def test_explicit_flags_win_and_skip_detection(self):
        a = self.args(apps_dir="/x/Apps", app_data="/x/data", remote_dir="/x/upd")
        dp.resolve_targets(object(), a)   # transport vô dụng: nếu dò sẽ nổ
        self.assertEqual((a.apps_dir, a.app_data, a.remote_dir), ("/x/Apps", "/x/data", "/x/upd"))

    def test_partial_flag_only_fills_the_rest(self):
        make_card(self.b, True)
        a = self.args(apps_dir="/custom/Apps")
        with contextlib.redirect_stdout(io.StringIO()):
            dp.resolve_targets(self.transport(), a)
        self.assertEqual(a.apps_dir, "/custom/Apps")
        self.assertEqual(a.remote_dir, posix(self.b) + "/LoongMate-update")


class TestNodesTailscaleBinFollowsUserdata(unittest.TestCase):
    """engine/nodes.py từng ghi cứng /mnt/sdcard/.userdata/mlp1/tailscale/tailscale: thẻ đổi chỗ thì
    Remote Nodes báo 'không thấy binary tailscale' dù Tailscale.pak vẫn cài đúng ở thẻ Leaf."""

    def test_binary_found_under_userdata_path_when_cards_swapped(self):
        from engine import nodes
        with tempfile.TemporaryDirectory() as td:
            ud = Path(td) / "ud"
            (ud / "tailscale").mkdir(parents=True)
            (ud / "tailscale" / "tailscale").write_bytes(b"x")
            with mock.patch.dict(os.environ, {"USERDATA_PATH": str(ud)}),                     mock.patch.object(nodes, "DEVICE_TS_BIN", str(Path(td) / "khong-co" / "tailscale")),                     mock.patch.object(nodes, "DEVICE_TS_SOCK", str(Path(td) / "khong-co.sock")),                     mock.patch.object(nodes.shutil, "which", return_value=None):
                self.assertEqual(nodes._resolve_ts(None, None)[0], str(ud / "tailscale" / "tailscale"))


if __name__ == "__main__":
    unittest.main()
