"""
Tests đợt C phía PC + cập nhật: pull_saves (offline/--keep/offsite.json), restore_menu, engine/update.py,
pc/deploy.py. Mọi test chạy hành vi thật (máy giả = thư mục tạm); không grep mã nguồn.

Chứng minh test biết đỏ: đặt LOONGMATE_PULL_PATH / LOONGMATE_UPDATE_PATH trỏ tới bản cố tình làm hỏng
của pull_saves.py / update.py thì các test tương ứng phải đỏ.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "pc"))

import test_pull_saves as tps  # noqa: E402  (FakeDevice, make_snapshot, Base;  ps = pull_saves đang được thử)

ps = tps.ps


def _load(name, override_env, default):
    path = os.environ.get(override_env) or str(default)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


up = _load("update_under_test", "LOONGMATE_UPDATE_PATH", ROOT / "engine" / "update.py")

# restore_menu và deploy import `pull_saves` trực tiếp: trỏ nó vào bản đang thử để test đỏ có hiệu lực
sys.modules["pull_saves"] = ps
rm = _load("restore_menu_under_test", "LOONGMATE_RESTORE_PATH", ROOT / "pc" / "restore_menu.py")
dp = _load("deploy_under_test", "LOONGMATE_DEPLOY_PATH", ROOT / "pc" / "deploy.py")


def sha(b):
    return hashlib.sha256(b).hexdigest()


class DeviceWithOffsite(tps.FakeDevice):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.offsite = None
        self.offsite_writes = 0

    def write_offsite(self, data):
        self.offsite = bytes(data)
        self.offsite_writes += 1


class OfflineDevice:
    def list_objects(self):
        raise ps.Unreachable("máy ngủ")

    list_snapshot_ids = list_objects

    def fetch(self, relpaths):
        raise ps.Unreachable("máy ngủ")

    def write_offsite(self, data):
        raise AssertionError("không được ghi offsite khi máy offline")


class PcBase(tps.Base):
    def setUp(self):
        super().setUp()
        self.dev = DeviceWithOffsite(self.dev_vault, self.dest)
        self.log = str(Path(self._tmp.name) / "pc" / "pull.log")

    def main(self, *args, transport=None):
        return ps.main(["--dest", str(self.dest), "--log", self.log, *args], transport=transport or self.dev)


# --------------------------------------------------------------------------- pull_saves

class TestPullOfflineAndKeep(PcBase):
    def test_01_offline_pull_creates_no_directories(self):
        with self.assertRaises(ps.Unreachable):
            ps.pull(OfflineDevice(), self.dest)
        self.assertFalse(self.dest.exists(), "máy offline mà vẫn tạo thư mục đích")

    def test_02_offline_main_returns_2_and_leaves_no_dest(self):
        self.assertEqual(self.main(transport=OfflineDevice()), 2)
        self.assertFalse(self.dest.exists())
        self.assertFalse((self.dest / "objects").exists())

    def test_03_keep_prunes_after_successful_pull(self):
        self.assertEqual(self.main("--keep", "1"), 0)
        self.assertEqual(sorted(ps.local_snapshots(self.dest)), ["snap_2"])
        self.assertFalse(self.obj(self.B).exists(), "object chỉ snap_1 dùng phải bị GC")
        self.assertTrue(self.obj(self.A).exists())
        self.assertTrue(self.obj(self.C).exists())

    def test_04_keep_does_not_prune_when_pull_has_errors(self):
        self.assertEqual(self.main(), 0)
        D = b"D" * 700
        tps.make_snapshot(self.dev_vault, "snap_3", {"Saves/d.srm": D})
        self.dev.corrupt[sha(D)] = b"X" * len(D)
        self.assertEqual(self.main("--keep", "1"), 1)
        self.assertEqual(sorted(ps.local_snapshots(self.dest)), ["snap_1", "snap_2"],
                         "pull lỗi thì tuyệt đối không được xoá snapshot")

    def test_05_keep_rejects_zero(self):
        self.assertEqual(self.main("--keep", "0"), 3)

    def test_05b_prune_alias_still_works(self):
        self.assertEqual(self.main("--prune", "1"), 0)
        self.assertEqual(len(ps.local_snapshots(self.dest)), 1)


class TestOffsite(PcBase):
    def test_10_offsite_written_with_exact_format_after_success(self):
        t0 = int(time.time())
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.dev.offsite_writes, 1)
        rec = json.loads(self.dev.offsite.decode("utf-8"))
        self.assertEqual(set(rec), {"schema", "last_pull_ts", "last_pull_at", "pc_host", "snapshot_ids",
                                    "objects", "verified"})
        self.assertEqual(rec["schema"], 1)
        self.assertIs(rec["verified"], True)
        self.assertIsInstance(rec["last_pull_ts"], int)
        self.assertTrue(t0 <= rec["last_pull_ts"] <= int(time.time()) + 1)
        self.assertRegex(rec["last_pull_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["last_pull_ts"])),
                         rec["last_pull_at"])
        self.assertEqual(rec["pc_host"], socket.gethostname())
        self.assertEqual(rec["snapshot_ids"], ["snap_1", "snap_2"])
        self.assertEqual(rec["objects"], 3)

    def test_11_offsite_reflects_keep(self):
        self.assertEqual(self.main("--keep", "1"), 0)
        rec = json.loads(self.dev.offsite)
        self.assertEqual((rec["snapshot_ids"], rec["objects"]), (["snap_2"], 2))

    def test_12_offsite_not_written_when_pull_has_errors(self):
        self.dev.offsite = b"old-mark"
        self.dev.corrupt[sha(self.B)] = b"X" * len(self.B)
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.dev.offsite_writes, 0)
        self.assertEqual(self.dev.offsite, b"old-mark", "mốc cũ phải giữ nguyên khi pull lỗi")

    def test_13_offsite_not_written_when_device_unreachable(self):
        self.assertEqual(self.main(transport=OfflineDevice()), 2)

    def test_14_offsite_not_written_when_local_verify_fails(self):
        real_pull = ps.pull

        def pull_then_rot(t, d):
            st = real_pull(t, d)
            self.obj(self.A).write_bytes(b"rot" * 100)  # hỏng sau khi kéo, trước khi verify
            return st
        with mock.patch.object(ps, "pull", pull_then_rot):
            self.assertEqual(self.main(), 1)
        self.assertEqual(self.dev.offsite_writes, 0)

    def test_15_offsite_not_written_when_pc_has_no_snapshot(self):
        for p in sorted(self.dev_vault.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.dev.offsite_writes, 0, "chưa có snapshot nào thì chưa có bản sao ngoài máy")

    def test_16_second_pull_with_nothing_new_still_refreshes_timestamp(self):
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.dev.offsite_writes, 2)

    def test_17_ssh_write_offsite_never_moves_unverified_partial(self):
        t = ps.SshTransport(vault="/mnt/x/LoongMate/SaveVault")
        cmds = []

        def fake_run(remote, timeout=90):
            cmds.append(remote)
            if remote.startswith("sha256sum"):
                return b"0" * 64 + b"  /mnt/x/LoongMate/offsite.json.partial\n"  # sai hash (vd 0 byte)
            return b""
        ok = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(ps.subprocess, "run", return_value=ok) as run, \
                mock.patch.object(t, "_run", side_effect=fake_run):
            with self.assertRaises(ps.TransportError):
                t.write_offsite(b'{"schema": 1}')
        self.assertEqual(run.call_args.kwargs["input"], b'{"schema": 1}')
        self.assertIn("/mnt/x/LoongMate/offsite.json.partial", run.call_args.args[0][-1])
        self.assertFalse([c for c in cmds if c.startswith("mv ")], "đã mv một file .partial chưa kiểm hash")

    def test_18_ssh_write_offsite_happy_path_verifies_then_moves_then_reads_back(self):
        t = ps.SshTransport(vault="/mnt/x/LoongMate/SaveVault")
        data = b'{"schema": 1}'
        cmds = []

        def fake_run(remote, timeout=90):
            cmds.append(remote)
            if remote.startswith("sha256sum"):
                return (sha(data) + "  f\n").encode()
            if remote.startswith("cat "):
                return data
            return b""
        ok = subprocess.CompletedProcess([], 0, b"", b"")
        with mock.patch.object(ps.subprocess, "run", return_value=ok), mock.patch.object(t, "_run", side_effect=fake_run):
            t.write_offsite(data)
        self.assertEqual([c.split()[0] for c in cmds], ["sha256sum", "mv", "cat"])
        self.assertIn("'/mnt/x/LoongMate/offsite.json'", cmds[1])


# --------------------------------------------------------------------------- restore_menu

class TestRestoreMenu(PcBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.main(), 0)  # PC đã có 2 snapshot
        self.target_vault = Path(self._tmp.name) / "newcard" / "SaveVault"
        self.target = tps.FakeDevice(self.target_vault, self.dest)

    def run_menu(self, *args, transport=None, tty=False, answers=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(rm, "_is_tty", return_value=tty), \
                mock.patch("builtins.input", side_effect=list(answers)):
            rc = rm.main(["--dest", str(self.dest), *args], transport=transport or self.target)
        return rc, out.getvalue(), err.getvalue()

    def test_20_list_reports_snapshots_and_on_device_flag(self):
        tps.make_snapshot(self.target_vault, "snap_1", {"Saves/a.srm": self.A, "Saves/b.srm": self.B})
        rows, err = rm.list_snapshots(self.dest, self.target)
        self.assertIsNone(err)
        by = {r["id"]: r for r in rows}
        self.assertEqual(set(by), {"snap_1", "snap_2"})
        self.assertEqual((by["snap_1"]["files"], by["snap_1"]["bytes"]), (2, 3000))
        self.assertEqual((by["snap_2"]["files"], by["snap_2"]["bytes"]), (2, 4000))
        self.assertIs(by["snap_1"]["on_device"], True)
        self.assertIs(by["snap_2"]["on_device"], False)

    def test_21_list_json_main(self):
        rc, out, _ = self.run_menu("--list", "--json")
        self.assertEqual(rc, 0)
        j = json.loads(out)
        self.assertTrue(j["ok"])
        self.assertEqual(sorted(s["id"] for s in j["snapshots"]), ["snap_1", "snap_2"])

    def test_22_list_when_device_unreachable_still_lists_with_unknown(self):
        rc, out, _ = self.run_menu("--list", "--json", transport=OfflineDevice())
        self.assertEqual(rc, 0)
        j = json.loads(out)
        self.assertTrue(j["device_error"])
        self.assertTrue(all(s["on_device"] is None for s in j["snapshots"]))

    def test_23_push_json_pushes_objects_first_manifest_last_and_tells_next_step(self):
        rc, out, _ = self.run_menu("--push", "snap_2", "--yes", "--json")
        self.assertEqual(rc, 0, out)
        j = json.loads(out)
        self.assertTrue(j["ok"])
        self.assertEqual((j["objects_pushed"], j["already_on_device"]), (2, False))
        self.assertEqual(j["next_step"], "Trên máy: LoongMate → SaveVault → chọn snapshot snap_2 → "
                                         "Xem trước khôi phục → Khôi phục")
        order = [rel for _, rels in self.target.events for rel in rels]
        self.assertEqual(order[-1], "snapshots/snap_2/manifest.json")
        self.assertTrue(all(r.startswith("objects/") for r in order[:-1]))
        for d in (self.A, self.C):
            self.assertEqual((self.target_vault / "objects" / sha(d)[:2] / sha(d)).read_bytes(), d)

    def test_24_push_already_on_device_pushes_nothing(self):
        tps.make_snapshot(self.target_vault, "snap_1", {"Saves/a.srm": self.A, "Saves/b.srm": self.B})
        rc, out, _ = self.run_menu("--push", "snap_1", "--yes", "--json")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["already_on_device"])
        self.assertEqual(self.target.events, [])

    def test_25_push_unknown_snapshot_fails_cleanly(self):
        rc, out, _ = self.run_menu("--push", "snap_nope", "--yes", "--json")
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])
        self.assertEqual(self.target.events, [])

    def test_26_push_refuses_corrupt_local_object(self):
        self.obj(self.C).write_bytes(b"corrupt-bytes")
        rc, out, _ = self.run_menu("--push", "snap_2", "--yes", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(self.target.events, [], "không được đẩy gì khi object trên PC hỏng")

    def test_27_non_tty_without_args_is_usage_error(self):
        rc, _, err = self.run_menu(tty=False)
        self.assertEqual(rc, 3)
        self.assertTrue(err)

    def test_28_push_without_yes_non_tty_refuses(self):
        rc, _, _ = self.run_menu("--push", "snap_2", "--json")
        self.assertEqual(rc, 3)
        self.assertEqual(self.target.events, [])

    def test_29_interactive_pick_confirm_pushes_and_prints_instructions(self):
        rows, _ = rm.list_snapshots(self.dest, self.target)
        idx = [r["id"] for r in rows].index("snap_1") + 1
        rc, out, _ = self.run_menu(tty=True, answers=[str(idx), "yes"])
        self.assertEqual(rc, 0, out)
        self.assertIn("Trên máy: LoongMate → SaveVault → chọn snapshot snap_1 → Xem trước khôi phục → Khôi phục", out)
        self.assertIn("snapshots/snap_1/manifest.json", [r for _, rels in self.target.events for r in rels])
        self.assertNotIn("snap_2", "".join(r for _, rels in self.target.events for r in rels if "manifest" in r))

    def test_30_interactive_decline_pushes_nothing(self):
        rc, _, _ = self.run_menu(tty=True, answers=["1", "no"])
        self.assertEqual(rc, 3)
        self.assertEqual(self.target.events, [])


# --------------------------------------------------------------------------- engine/update.py

def write_tree(base, files):
    for rel, data in files.items():
        p = Path(base).joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def make_bundle(base, files, version="v2", extra_manifest=None):
    write_tree(base, files)
    m = {"schema": 1, "version": version, "created_at": "2026-10-02 10:00:00",
         "files": {rel: sha(d) for rel, d in files.items()}}
    m.update(extra_manifest or {})
    Path(base, "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return m


V1 = {"LoongMate.pak/launch.sh": b"#!/bin/sh\necho v1\n", "LoongMate.pak/engine/cli.py": b"print('v1')\n",
      "LoongMate.pak/bin/tool": b"BIN1", "LoongMateAgent.pak/launch.sh": b"#!/bin/sh\nagent1\n",
      "LoongMateNative.pak/launch.sh": b"#!/bin/sh\nnative1\n", "LoongMateNative.pak/bin/lm-home": b"NATIVE1"}
V2 = {"LoongMate.pak/launch.sh": b"#!/bin/sh\necho v2\n", "LoongMate.pak/engine/cli.py": b"print('v2')\n",
      "LoongMate.pak/engine/new.py": b"NEW\n", "LoongMate.pak/bin/tool": b"BIN2",
      "LoongMateAgent.pak/launch.sh": b"#!/bin/sh\nagent2\n",
      "LoongMateNative.pak/launch.sh": b"#!/bin/sh\nnative2\n", "LoongMateNative.pak/bin/lm-home": b"NATIVE2"}


def tree(base):
    base = Path(base)
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


class UpdBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.apps, self.data, self.bundle = root / "Apps", root / "userdata" / "LoongMate", root / "bundle"
        write_tree(self.apps, V1)
        write_tree(self.apps, {"Other.pak/launch.sh": b"other", "Other.pak/keep.txt": b"keep"})
        write_tree(root, {"Roms/GB/game.gb": b"ROM", "Saves/GB/game.srm": b"SAVE", "States/s.st": b"STATE"})
        self.outside = {p: tree(root / p) for p in ("Roms", "Saves", "States")}
        self.root = root
        self.manifest = make_bundle(self.bundle, V2)

    def installed_paks(self):
        return {k: v for k, v in tree(self.apps).items() if k.split("/")[0] in up.PAKS}

    def assert_outside_untouched(self):
        for p, before in self.outside.items():
            self.assertEqual(tree(self.root / p), before, f"{p} bị đụng tới")
        self.assertEqual(tree(self.apps / "Other.pak"), {"launch.sh": b"other", "keep.txt": b"keep"})

    def assert_no_leftovers(self):
        left = [p.name for p in self.apps.iterdir() if p.name.startswith(".")]
        self.assertEqual(left, [], "còn thư mục tạm .new/.old")


class TestVerifyBundle(UpdBase):
    def test_40_good_bundle_ok(self):
        r = up.verify_bundle(self.bundle)
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["version"], r["files"]), ("v2", len(V2)))
        self.assertEqual(r["paks"], sorted(up.PAKS))

    def test_41_wrong_hash_is_caught(self):
        (self.bundle / "LoongMate.pak/engine/cli.py").write_bytes(b"print('tampered')\n")
        r = up.verify_bundle(self.bundle)
        self.assertFalse(r["ok"])
        self.assertTrue(any("cli.py" in e and "sha256" in e for e in r["errors"]), r["errors"])

    def test_42_extra_file_is_caught(self):
        write_tree(self.bundle, {"LoongMate.pak/engine/evil.py": b"x"})
        r = up.verify_bundle(self.bundle)
        self.assertFalse(r["ok"])
        self.assertTrue(any("evil.py" in e and "thừa" in e for e in r["errors"]), r["errors"])

    def test_43_missing_file_is_caught(self):
        (self.bundle / "LoongMate.pak/bin/tool").unlink()
        self.assertFalse(up.verify_bundle(self.bundle)["ok"])

    def test_44_paths_escaping_or_outside_paks_are_rejected(self):
        for bad in ("../outside.txt", "LoongMate.pak/../../x", "/etc/passwd", "Roms/GB/x", "Saves/x",
                    "LoongMate.pak\\evil", "LoongMate.pak", "Other.pak/launch.sh", "LoongMate.pak/./a"):
            with self.subTest(bad=bad):
                b = self.root / ("b_" + hashlib.md5(bad.encode()).hexdigest()[:6])
                make_bundle(b, {"LoongMate.pak/launch.sh": b"x"})
                m = json.loads((b / "manifest.json").read_text())
                m["files"][bad] = sha(b"x")
                (b / "manifest.json").write_text(json.dumps(m))
                r = up.verify_bundle(b)
                self.assertFalse(r["ok"], r)
                self.assertTrue(any(bad in e or repr(bad) in e for e in r["errors"]), r["errors"])

    def test_45_bad_manifests(self):
        for body in ("không phải json", json.dumps({"schema": 2, "version": "v", "files": {"LoongMate.pak/a": "0" * 64}}),
                     json.dumps({"schema": 1, "version": "../v", "files": {"LoongMate.pak/a": "0" * 64}}),
                     json.dumps({"schema": 1, "version": "v", "files": {}}),
                     json.dumps({"schema": 1, "version": "v", "files": {"LoongMate.pak/a": "zz"}})):
            with self.subTest(body=body[:30]):
                (self.bundle / "manifest.json").write_text(body, encoding="utf-8")
                self.assertFalse(up.verify_bundle(self.bundle)["ok"])

    def test_46_missing_manifest(self):
        (self.bundle / "manifest.json").unlink()
        self.assertFalse(up.verify_bundle(self.bundle)["ok"])

    def test_47_symlink_in_bundle_rejected(self):
        link = self.bundle / "LoongMate.pak" / "link"
        try:
            os.symlink(self.bundle / "LoongMate.pak" / "launch.sh", link)
        except (OSError, NotImplementedError):
            self.skipTest("không tạo được symlink trên máy này")
        self.assertFalse(up.verify_bundle(self.bundle)["ok"])


class TestApply(UpdBase):
    def test_50_apply_success_replaces_paks_and_records_version(self):
        up_prev = {"schema": 1, "version": "v1"}
        (self.data).mkdir(parents=True)
        (self.data / "installed-version.json").write_text(json.dumps(up_prev))
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.installed_paks(), V2)
        iv = json.loads((self.data / "installed-version.json").read_text())
        self.assertEqual((iv["version"], iv["previous_version"]), ("v2", "v1"))
        self.assertEqual(up.status(self.data)["installed"]["version"], "v2")
        self.assert_no_leftovers()
        self.assert_outside_untouched()
        bk = self.data / "pak-backup" / r["backup"]
        self.assertEqual(tree(bk / "LoongMate.pak"),
                         {k.split("/", 1)[1]: v for k, v in V1.items() if k.startswith("LoongMate.pak/")})
        self.assertEqual(up.status(self.data)["backups"][0]["version_before"], "v1")

    @unittest.skipIf(os.name == "nt", "Windows không có bit thực thi; đã kiểm trên máy thật qua deploy.py")
    def test_51_launch_sh_and_bin_are_executable_after_apply(self):
        self.assertTrue(up.apply_update(self.bundle, self.apps, self.data)["ok"])
        for rel in ("LoongMate.pak/launch.sh", "LoongMate.pak/bin/tool", "LoongMateAgent.pak/launch.sh",
                    "LoongMateNative.pak/launch.sh", "LoongMateNative.pak/bin/lm-home"):
            self.assertTrue(os.stat(self.apps / rel).st_mode & stat.S_IXUSR, rel)
        self.assertFalse(os.stat(self.apps / "LoongMate.pak/engine/cli.py").st_mode & stat.S_IXUSR)

    def test_52_bad_bundle_is_refused_and_nothing_changes(self):
        (self.bundle / "LoongMate.pak/engine/cli.py").write_bytes(b"tampered")
        before = tree(self.apps)
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(r["stage"], "verify")
        self.assertEqual(tree(self.apps), before)
        self.assertFalse(self.data.exists())

    def test_53_failure_midway_through_swap_restores_every_pak(self):
        real, calls = up._swap_one, []

        def flaky(apps, name):
            calls.append(name)
            if len(calls) == 2:
                raise OSError("giả lập: hết chỗ/đột ngột mất thẻ")
            return real(apps, name)
        with mock.patch.object(up, "_swap_one", flaky):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertEqual(len(calls), 2, "test phải thật sự đã đổi pak đầu rồi mới lỗi")
        self.assertFalse(r["ok"])
        self.assertTrue(r["rolled_back"])
        self.assertEqual(self.installed_paks(), V1, "bản cũ phải còn NGUYÊN VẸN")
        self.assert_no_leftovers()
        self.assert_outside_untouched()
        self.assertFalse((self.data / "installed-version.json").exists())

    def test_54_failure_in_the_middle_of_one_pak_rename_restores(self):
        """rename .old xong nhưng rename .new -> tên thật lỗi."""
        real_rename, n = os.rename, [0]

        def flaky(src, dst):
            if str(dst).endswith("LoongMateAgent.pak") and ".new" in str(src):
                raise OSError("giả lập")
            return real_rename(src, dst)
        with mock.patch.object(up.os, "rename", flaky):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(self.installed_paks(), V1)
        self.assert_no_leftovers()

    def test_55_failure_writing_version_after_swap_restores_old_paks(self):
        real = up._write_json_atomic

        def flaky(path, obj):
            if Path(path).name == "installed-version.json":
                raise OSError("giả lập: đầy đĩa")
            return real(path, obj)
        with mock.patch.object(up, "_write_json_atomic", flaky):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(self.installed_paks(), V1)
        self.assert_no_leftovers()

    def test_56_corrupt_copy_to_card_is_detected_before_swap(self):
        real = up._fix_modes

        def corrupt(pak_dir, name):
            real(pak_dir, name)
            if name == "LoongMate.pak":
                (Path(pak_dir) / "engine" / "cli.py").write_bytes(b"bit-rot")
        with mock.patch.object(up, "_fix_modes", corrupt):
            r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(self.installed_paks(), V1)
        self.assert_no_leftovers()

    def test_57_only_three_backups_kept(self):
        for i in range(5):
            files = dict(V2)
            files["LoongMate.pak/engine/cli.py"] = f"print({i})\n".encode()
            shutil_b = self.root / f"b{i}"
            make_bundle(shutil_b, files, version=f"v{i + 10}")
            self.assertTrue(up.apply_update(shutil_b, self.apps, self.data)["ok"])
        self.assertEqual(len(list((self.data / "pak-backup").iterdir())), 3)
        self.assertEqual(up.status(self.data)["installed"]["version"], "v14")

    def test_58_partial_bundle_only_touches_its_paks(self):
        b = self.root / "partial"
        make_bundle(b, {"LoongMate.pak/launch.sh": b"#!/bin/sh\necho only\n"}, version="p1")
        self.assertTrue(up.apply_update(b, self.apps, self.data)["ok"])
        got = self.installed_paks()
        self.assertEqual(got["LoongMate.pak/launch.sh"], b"#!/bin/sh\necho only\n")
        self.assertEqual(got["LoongMateAgent.pak/launch.sh"], V1["LoongMateAgent.pak/launch.sh"])
        self.assertNotIn("LoongMate.pak/engine/cli.py", got, "pak được thay trọn, không trộn file cũ")

    def test_59_stale_tmp_dirs_from_crashed_run_do_not_break_apply(self):
        write_tree(self.apps, {".LoongMate.pak.new/junk": b"j", ".LoongMate.pak.old/junk": b"j"})
        r = up.apply_update(self.bundle, self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.installed_paks(), V2)
        self.assert_no_leftovers()

    def test_59b_missing_apps_dir(self):
        r = up.apply_update(self.bundle, self.root / "nope", self.data)
        self.assertFalse(r["ok"])
        self.assertFalse((self.root / "nope").exists())


class TestRollback(UpdBase):
    def test_60_rollback_restores_old_paks_and_version(self):
        self.data.mkdir(parents=True)
        (self.data / "installed-version.json").write_text(json.dumps({"schema": 1, "version": "v1"}))
        self.assertTrue(up.apply_update(self.bundle, self.apps, self.data)["ok"])
        self.assertEqual(self.installed_paks(), V2)
        r = up.rollback(self.apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.installed_paks(), V1)
        iv = json.loads((self.data / "installed-version.json").read_text())
        self.assertEqual((iv["version"], iv["rolled_back_from"]), ("v1", "v2"))
        self.assert_no_leftovers()
        self.assert_outside_untouched()

    def test_61_rollback_without_backup_fails_and_changes_nothing(self):
        before = tree(self.apps)
        r = up.rollback(self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(tree(self.apps), before)

    def test_62_rollback_removes_pak_that_did_not_exist_before(self):
        shutil_apps = self.root / "Apps2"
        write_tree(shutil_apps, {k: v for k, v in V1.items() if k.startswith("LoongMate.pak/")})
        self.assertTrue(up.apply_update(self.bundle, shutil_apps, self.data)["ok"])
        self.assertTrue((shutil_apps / "LoongMateAgent.pak").is_dir())
        r = up.rollback(shutil_apps, self.data)
        self.assertTrue(r["ok"], r)
        self.assertFalse((shutil_apps / "LoongMateAgent.pak").exists())
        self.assertEqual({k: v for k, v in tree(shutil_apps).items()},
                         {k: v for k, v in V1.items() if k.startswith("LoongMate.pak/")})

    def test_63_rollback_failure_midway_keeps_current_version(self):
        self.assertTrue(up.apply_update(self.bundle, self.apps, self.data)["ok"])
        real, calls = up._swap_one, []

        def flaky(apps, name):
            calls.append(name)
            if len(calls) == 2:
                raise OSError("giả lập")
            return real(apps, name)
        with mock.patch.object(up, "_swap_one", flaky):
            r = up.rollback(self.apps, self.data)
        self.assertFalse(r["ok"])
        self.assertEqual(self.installed_paks(), V2, "rollback lỗi thì phải còn nguyên bản đang chạy")
        self.assert_no_leftovers()


class TestUpdateCli(UpdBase):
    def run_cli(self, *args, tty=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(up.sys.stdin, "isatty", return_value=tty):
            rc = up.main(list(args))
        return rc, out.getvalue()

    def test_70_cli_verify_apply_status_rollback(self):
        opts = ["--apps-dir", str(self.apps), "--app-data", str(self.data)]
        rc, out = self.run_cli("verify", str(self.bundle), *opts)
        self.assertEqual((rc, json.loads(out)["ok"]), (0, True))
        rc, _ = self.run_cli("apply", str(self.bundle), *opts)
        self.assertEqual(rc, 3, "không --yes và không tty thì phải từ chối")
        self.assertEqual(self.installed_paks(), V1)
        rc, out = self.run_cli("apply", str(self.bundle), *opts, "--yes")
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli("status", *opts)
        self.assertEqual(json.loads(out)["installed"]["version"], "v2")
        rc, out = self.run_cli("rollback", *opts, "--yes")
        self.assertEqual(rc, 0)
        self.assertEqual(self.installed_paks(), V1)

    def test_71_cli_verify_bad_bundle_exit_1(self):
        (self.bundle / "LoongMate.pak/bin/tool").write_bytes(b"x")
        rc, out = self.run_cli("verify", str(self.bundle))
        self.assertEqual((rc, json.loads(out)["ok"]), (1, False))


# --------------------------------------------------------------------------- pc/deploy.py

def run_git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TestDeploy(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        files = {f"pak/build/{k}": v for k, v in V1.items() if k.startswith("LoongMate.pak/")}
        files["pak/build/LoongMate.pak/engine/update.py"] = (ROOT / "engine" / "update.py").read_bytes()
        files["pak/build/LoongMate.pak/engine/__pycache__/x.pyc"] = b"junk"
        files["pak/Apps/mlp1/LoongMateAgent.pak/launch.sh"] = b"agent"
        files["pak/build/LoongMate.pak/bin/lm-home"] = b"native"   # từ khi gộp app: native nằm TRONG LoongMate.pak
        write_tree(self.repo, files)
        (self.repo / ".gitignore").write_text("pak/build/\n")
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "init")

    def test_80_dirty_tree_refused_unless_allowed(self):
        self.assertRegex(dp.resolve_version(self.repo, False), r"^[0-9a-f]{7,}$")
        (self.repo / "pak/Apps/mlp1/LoongMateAgent.pak/launch.sh").write_bytes(b"changed")
        with self.assertRaises(dp.DeployError):
            dp.resolve_version(self.repo, False)
        self.assertTrue(dp.resolve_version(self.repo, True).endswith("-dirty"))

    def test_81_make_bundle_has_all_paks_skips_pycache_and_verifies(self):
        out = Path(self._tmp.name) / "bundle"
        m = dp.make_bundle(dp.sources(self.repo), out, "abc1234", dp.REMOVE_PAKS)
        self.assertNotIn("LoongMate.pak/engine/__pycache__/x.pyc", m["files"])
        self.assertFalse((out / "LoongMate.pak/engine/__pycache__").exists())
        self.assertIn("LoongMateAgent.pak/launch.sh", m["files"])
        self.assertIn("LoongMate.pak/bin/lm-home", m["files"])        # đổi từ LoongMateNative.pak/bin/lm-home khi gộp app
        self.assertNotIn("LoongMateNative.pak", {k.split("/")[0] for k in m["files"]})
        v = dp.load_update_module(out).verify_bundle(out)
        self.assertTrue(v["ok"], v)
        self.assertEqual(m["files"]["LoongMate.pak/launch.sh"], sha(V1["LoongMate.pak/launch.sh"]))

    def test_82_dry_run_builds_and_verifies_without_touching_device(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(dp.pull_saves, "SshTransport",
                                                                side_effect=AssertionError("không được ssh")):
            rc = dp.main(["--dry-run", "--offline", "--skip-build", "--root", str(self.repo)])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("verify trên PC OK", out.getvalue())

    def test_83_dry_run_refuses_dirty_tree(self):
        (self.repo / "dirty.txt").write_text("x")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = dp.main(["--dry-run", "--offline", "--skip-build", "--root", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("bẩn", err.getvalue())

    def test_84_bundle_from_deploy_applies_with_update_module(self):
        """Gói do deploy dựng phải cài được bằng chính update.py nằm trong gói."""
        out = Path(self._tmp.name) / "bundle"
        dp.make_bundle(dp.sources(self.repo), out, "abc1234", dp.REMOVE_PAKS)
        mod = dp.load_update_module(out)
        apps, data = Path(self._tmp.name) / "Apps", Path(self._tmp.name) / "data"
        write_tree(apps, {"LoongMate.pak/launch.sh": b"old"})
        r = mod.apply_update(out, apps, data)
        self.assertTrue(r["ok"], r)
        self.assertEqual((apps / "LoongMate.pak/bin/lm-home").read_bytes(), b"native")   # đổi từ LoongMateNative.pak


if __name__ == "__main__":
    unittest.main()
