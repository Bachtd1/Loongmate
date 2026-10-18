"""
Tests cho pc/pull_saves.py với một "máy giả" (FakeDevice) thay cho ssh.

FakeDevice đọc/ghi một thư mục tạm có đúng layout vault của máy. Mọi test chạy hành vi thật của
pull/verify/prune/push; không grep mã nguồn. Để chứng minh test biết đỏ, đặt
LOONGMATE_PULL_PATH trỏ tới một bản pull_saves.py cố tình làm hỏng - test sẽ chạy bản đó.
"""
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_override = os.environ.get("LOONGMATE_PULL_PATH")
_path = _override or str(ROOT / "pc" / "pull_saves.py")
_spec = importlib.util.spec_from_file_location("pull_saves_under_test", _path)
ps = importlib.util.module_from_spec(_spec)
sys.modules["pull_saves_under_test"] = ps
_spec.loader.exec_module(ps)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_snapshot(vault, sid, files, committed=True, layout="objects"):
    """files: {rel: bytes}. Ghi object + manifest như engine/savevault.py."""
    vault = Path(vault)
    entries = {}
    for rel, data in files.items():
        h = sha(data)
        p = vault / "objects" / h[:2] / h
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        entries[rel] = {"rel_path": rel, "size": len(data), "sha256": h}
    mf = vault / "snapshots" / sid / "manifest.json"
    mf.parent.mkdir(parents=True, exist_ok=True)
    mf.write_text(json.dumps({"snapshot_id": sid, "committed": committed, "layout": layout,
                              "files": entries}), encoding="utf-8")


class FakeDevice:
    """Thay SshTransport. corrupt: {hash: bytes} nội dung sai được gửi thay bản thật."""

    def __init__(self, vault, local_dest=None):
        self.vault = Path(vault)
        self.vault.mkdir(parents=True, exist_ok=True)
        self.local_dest = local_dest
        self.corrupt = {}
        self.hidden = set()      # hash có trên đĩa nhưng "không có" trong listing
        self.fetched = []        # mọi relpath từng được yêu cầu
        self.events = []         # ("push", [rel...]) theo thứ tự
        self.manifest_seen_during_object_fetch = False

    def list_objects(self):
        res = {}
        for p in (self.vault / "objects").rglob("*") if (self.vault / "objects").exists() else []:
            if p.is_file() and p.name not in self.hidden:
                res[p.name] = p.stat().st_size
        return res

    def list_snapshot_ids(self):
        d = self.vault / "snapshots"
        return sorted(x.name for x in d.iterdir()) if d.exists() else []

    def fetch(self, relpaths):
        for rel in relpaths:
            self.fetched.append(rel)
            if rel.startswith("objects/") and self.local_dest is not None:
                if any((Path(self.local_dest) / "snapshots").glob("*/manifest.json")) and \
                        getattr(self, "expect_no_manifest", False):
                    self.manifest_seen_during_object_fetch = True
            p = self.vault / rel
            if not p.is_file():
                continue
            data = self.corrupt.get(p.name, p.read_bytes())
            yield rel, io.BytesIO(data)

    def write_offsite(self, data):
        self.offsite = bytes(data)

    def push(self, members):
        self.events.append(("push", [rel for rel, _ in members]))
        for rel, src in members:
            data = bytes(src) if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
            if rel.startswith("objects/") and sha(data) != rel.rsplit("/", 1)[-1]:
                raise ps.TransportError("hash lệch trên máy giả")
            p = self.vault / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._close_log)  # Windows không xoá được thư mục tạm khi log còn mở

        root = Path(self._tmp.name)
        self.dev_vault = root / "device" / "SaveVault"
        self.dest = root / "pc" / "SaveVault"
        self.dev = FakeDevice(self.dev_vault, self.dest)
        self.A = b"A" * 1000
        self.B = b"B" * 2000
        self.C = b"C" * 3000
        make_snapshot(self.dev_vault, "snap_1", {"Saves/a.srm": self.A, "Saves/b.srm": self.B})
        make_snapshot(self.dev_vault, "snap_2", {"Saves/a.srm": self.A, "Saves/c.srm": self.C})

    @staticmethod
    def _close_log():
        for h in list(ps.log.handlers):
            h.close()
            ps.log.removeHandler(h)

    def obj(self, data):
        return ps.obj_path(self.dest, sha(data))


class TestPull(Base):
    def test_01_first_pull_gets_everything_and_second_downloads_nothing(self):
        st = ps.pull(self.dev, self.dest)
        self.assertEqual((st["snapshots_new"], st["objects_downloaded"], st["errors"]), (2, 3, []))
        for d in (self.A, self.B, self.C):
            self.assertEqual(self.obj(d).read_bytes(), d)
        self.dev.fetched.clear()
        st2 = ps.pull(self.dev, self.dest)
        self.assertEqual((st2["snapshots_new"], st2["objects_downloaded"]), (0, 0))
        self.assertEqual(self.dev.fetched, [], "lần 2 không được yêu cầu gì từ máy")

    def test_02_only_new_objects_are_downloaded(self):
        ps.pull(self.dev, self.dest)
        D = b"D" * 500
        make_snapshot(self.dev_vault, "snap_3", {"Saves/a.srm": self.A, "Saves/d.srm": D})
        self.dev.fetched.clear()
        st = ps.pull(self.dev, self.dest)
        self.assertEqual((st["snapshots_new"], st["objects_downloaded"]), (1, 1))
        objs = [r for r in self.dev.fetched if r.startswith("objects/")]
        self.assertEqual(objs, [f"objects/{sha(D)[:2]}/{sha(D)}"])

    def test_03_corrupt_object_is_rejected_deleted_and_blocks_manifest(self):
        h = sha(self.B)
        self.dev.corrupt[h] = b"X" * len(self.B)  # cùng size, khác nội dung
        st = ps.pull(self.dev, self.dest)
        self.assertEqual(st["objects_bad"], 1)
        self.assertTrue(st["errors"])
        self.assertFalse(self.obj(self.B).exists(), "object hỏng phải bị xoá")
        self.assertFalse(list(self.dest.rglob("*.partial")))
        self.assertFalse((self.dest / "snapshots" / "snap_1" / "manifest.json").exists())
        self.assertTrue((self.dest / "snapshots" / "snap_2" / "manifest.json").exists())
        # Máy sửa xong -> lần sau tự lành
        self.dev.corrupt.clear()
        st2 = ps.pull(self.dev, self.dest)
        self.assertEqual((st2["errors"], st2["snapshots_new"]), ([], 1))
        self.assertEqual(self.obj(self.B).read_bytes(), self.B)

    def test_04_truncated_object_is_rejected(self):
        self.dev.corrupt[sha(self.C)] = self.C[:10]
        st = ps.pull(self.dev, self.dest)
        self.assertEqual(st["objects_bad"], 1)
        self.assertFalse(self.obj(self.C).exists())

    def test_05_manifest_written_only_after_all_objects(self):
        self.dev.expect_no_manifest = True
        ps.pull(self.dev, self.dest)
        self.assertFalse(self.dev.manifest_seen_during_object_fetch,
                         "manifest đã xuất hiện trên PC trong lúc còn đang tải object")
        self.assertEqual(len(ps.local_snapshots(self.dest)), 2)

    def test_06_snapshot_with_object_missing_on_device_is_not_committed_locally(self):
        self.dev.hidden.add(sha(self.B))
        st = ps.pull(self.dev, self.dest)
        self.assertFalse((self.dest / "snapshots" / "snap_1" / "manifest.json").exists())
        self.assertTrue((self.dest / "snapshots" / "snap_2" / "manifest.json").exists())
        self.assertEqual([s for s, _ in st["snapshots_skipped"]], ["snap_1"])

    def test_07_uncommitted_and_legacy_snapshots_are_skipped(self):
        make_snapshot(self.dev_vault, "snap_u", {"x": b"u"}, committed=False)
        make_snapshot(self.dev_vault, "snap_l", {"y": b"l"}, layout="legacy")
        st = ps.pull(self.dev, self.dest)
        skipped = dict(st["snapshots_skipped"])
        self.assertIn("snap_u", skipped)
        self.assertIn("snap_l", skipped)
        self.assertFalse((self.dest / "snapshots" / "snap_u").exists())
        self.assertFalse((self.dest / "snapshots" / "snap_l").exists())
        self.assertFalse(self.obj(b"u").exists())

    def test_08_pull_never_modifies_device(self):
        def snap(v):
            return {str(p.relative_to(v)): p.read_bytes() for p in v.rglob("*") if p.is_file()}
        before = snap(self.dev_vault)
        ps.pull(self.dev, self.dest)
        self.assertEqual(snap(self.dev_vault), before)

    def test_09_pulled_vault_opens_with_engine(self):
        from engine.savevault import SaveVault
        ps.pull(self.dev, self.dest)
        v = SaveVault(self.dest.parent / "x", None, self.dest.parent)
        ids = [s["id"] for s in v.list_snapshots()]
        self.assertEqual(sorted(ids), ["snap_1", "snap_2"])
        self.assertTrue(all(v.verify_snapshot(i)["ok"] for i in ids))


class TestVerifyPrune(Base):
    def test_10_verify_ok_then_detects_bitrot_and_pull_heals(self):
        ps.pull(self.dev, self.dest)
        self.assertTrue(ps.verify(self.dest)["ok"])
        self.obj(self.A).write_bytes(b"Z" * len(self.A))  # bit rot cùng size
        r = ps.verify(self.dest)
        self.assertFalse(r["ok"])
        self.assertEqual(r["bad_objects"], [sha(self.A)])
        self.assertEqual(set(r["bad_snapshots"]), {"snap_1", "snap_2"})
        st = ps.pull(self.dev, self.dest)
        self.assertEqual(st["objects_downloaded"], 1)
        self.assertTrue(ps.verify(self.dest)["ok"])

    def test_11_prune_keeps_newest_and_gcs_unreferenced_objects(self):
        ps.pull(self.dev, self.dest)
        r = ps.prune(self.dest, 1)
        self.assertEqual(r["snapshots_removed"], ["snap_1"])
        self.assertEqual(r["objects_gc"], 1)  # chỉ B là mồ côi; A dùng chung với snap_2
        self.assertFalse(self.obj(self.B).exists())
        self.assertTrue(self.obj(self.A).exists() and self.obj(self.C).exists())
        self.assertEqual(list(ps.local_snapshots(self.dest)), ["snap_2"])
        self.assertTrue(ps.verify(self.dest)["ok"])

    def test_12_prune_never_touches_device(self):
        ps.pull(self.dev, self.dest)
        ps.prune(self.dest, 1)
        self.assertEqual(self.dev.list_snapshot_ids(), ["snap_1", "snap_2"])
        self.assertEqual(len(self.dev.list_objects()), 3)


class TestPush(Base):
    def setUp(self):
        super().setUp()
        ps.pull(self.dev, self.dest)
        self.target = FakeDevice(Path(self._tmp.name) / "newdev" / "SaveVault")

    def test_13_push_objects_first_manifest_last(self):
        r = ps.push_snapshot(self.target, self.dest, "snap_1")
        self.assertEqual((r["objects_pushed"], r["objects_already"]), (2, 0))
        last = self.target.events[-1][1]
        self.assertEqual(last, ["snapshots/snap_1/manifest.json"])
        for ev in self.target.events[:-1]:
            self.assertTrue(all(x.startswith("objects/") for x in ev[1]))
        self.assertEqual(self.target.list_snapshot_ids(), ["snap_1"])
        self.assertEqual(self.target.list_objects(), {sha(self.A): 1000, sha(self.B): 2000})

    def test_14_push_skips_objects_device_already_has_and_refuses_overwrite(self):
        ps.push_snapshot(self.target, self.dest, "snap_1")
        r = ps.push_snapshot(self.target, self.dest, "snap_2")
        self.assertEqual((r["objects_pushed"], r["objects_already"]), (1, 1))
        with self.assertRaises(ValueError):
            ps.push_snapshot(self.target, self.dest, "snap_2")

    def test_15_push_refuses_when_local_object_corrupt_and_writes_nothing(self):
        self.obj(self.B).write_bytes(b"Q" * len(self.B))
        with self.assertRaises(ValueError):
            ps.push_snapshot(self.target, self.dest, "snap_1")
        self.assertEqual(self.target.events, [])

    def test_16_push_rejects_bad_ids(self):
        for bad in ("../x", "snap_404", "a b"):
            with self.assertRaises(ValueError):
                ps.push_snapshot(self.target, self.dest, bad)

    def test_17_push_requires_confirmation(self):
        # không --yes và stdin không phải tty -> mã 3, máy không bị ghi
        from unittest import mock
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            rc = ps.main(["--dest", str(self.dest), "--log", str(Path(self._tmp.name) / "l.log"),
                          "--push-snapshot", "snap_1"], transport=self.target)
        self.assertEqual(rc, 3)
        self.assertEqual(self.target.events, [])


class TestMain(Base):
    def test_18_unreachable_exit_code_2(self):
        class Down:
            def list_objects(self):
                raise ps.Unreachable("máy ngủ")
        rc = ps.main(["--dest", str(self.dest), "--log", str(Path(self._tmp.name) / "l.log")], transport=Down())
        self.assertEqual(rc, 2)

    def test_19_main_returns_1_on_corruption_and_skips_prune(self):
        self.dev.corrupt[sha(self.B)] = b"X" * len(self.B)
        rc = ps.main(["--dest", str(self.dest), "--prune", "1", "--log", str(Path(self._tmp.name) / "l.log")],
                     transport=self.dev)
        self.assertEqual(rc, 1)
        self.assertTrue(self.obj(self.C).exists() and self.obj(self.A).exists(), "có lỗi thì không được prune")

    def test_20_main_ok_and_verify_exit_codes(self):
        log = str(Path(self._tmp.name) / "l.log")
        self.assertEqual(ps.main(["--dest", str(self.dest), "--log", log], transport=self.dev), 0)
        self.assertEqual(ps.main(["--dest", str(self.dest), "--log", log, "--verify"]), 0)
        self.obj(self.A).write_bytes(b"Z" * len(self.A))
        self.assertEqual(ps.main(["--dest", str(self.dest), "--log", log, "--verify"]), 1)


if __name__ == "__main__":
    unittest.main()
