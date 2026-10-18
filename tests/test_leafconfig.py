"""Sao lưu cấu hình Leaf/RetroArch: bố cục thư mục như trên máy thật (.umrk/mlp1/...)."""
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import leafconfig as LC  # noqa: E402

P = "mlp1"


class TestLeafConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm_leafcfg_"))
        self.sd, self.ud = self.tmp / "sd", self.tmp / "ud"
        u = self.sd / ".umrk" / P
        self.ra = u / "retroarch" / "retroarch.cfg"
        self.opt = u / "retroarch" / ".config" / "retroarch" / "config" / "mGBA" / "mGBA.opt"
        self.rmp = u / "retroarch" / ".config" / "retroarch" / "config" / "remaps" / "PCSX-ReARMed" / "PCSX-ReARMed.rmp"
        self.n64 = self.sd / ".userdata" / P / "mupen64plus" / "config" / "mupen64plus.cfg"
        self.db = u / "library.db"
        for p, t in ((self.ra, 'video_smooth = "false"\naudio_latency = "64"\n'), (self.opt, 'mgba_skip_bios = "OFF"\n'),
                     (self.rmp, 'input_player1_btn_a = "8"\n'), (self.n64, "[Core]\nScreenshotPath = \"\"\n"),
                     (u / "wifi.conf", "psk=secret\n"), (u / "release.json", '{"version": "0.11.0"}'),
                     (u / "retroarch" / ".config" / "retroarch" / "shaders" / "x.glslp", "shader\n")):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(t, encoding="utf-8")
        c = sqlite3.connect(str(self.db))
        c.execute("create table game_settings(k text, v text)")
        c.execute("insert into game_settings values ('core_id', 'mgba')")
        c.commit()
        c.close()
        self.ud.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, **kw):
        r = LC.snapshot(self.ud, self.sd, P, **kw)
        self.assertTrue(r["success"], r)
        return r

    def test_collects_config_but_never_wifi_or_bundled_shaders(self):
        files = LC.collect(self.sd, P)
        self.assertEqual(sorted(g for _p, g in files.values()), ["leaf_db", "overrides", "overrides", "retroarch", "standalone"])
        self.assertFalse(any("wifi" in k or k.endswith(".glslp") for k in files))

    def test_snapshot_dedupes_and_records_leaf_version(self):
        r1 = self.snap()
        self.assertTrue(r1["created"])
        self.assertEqual((r1["files"], r1["leaf_version"]), (5, "0.11.0"))
        r2 = self.snap()
        self.assertFalse(r2["created"], "không đổi gì thì không tạo bản mới")
        self.assertEqual(len(LC.list_snapshots(self.ud)), 1)
        (self.sd / ".umrk" / P / "release.json").write_text('{"version": "0.13.0"}', encoding="utf-8")
        self.assertTrue(self.snap(label="sau cập nhật Leaf")["created"], "đổi phiên bản Leaf thì phải có bản mới")

    def test_db_copy_is_a_valid_sqlite(self):
        r = self.snap()
        man = LC._manifest(self.ud, r["id"])
        sha = man["files"][".umrk/mlp1/library.db"]["sha256"]
        c = sqlite3.connect(str(LC._obj(self.ud, sha)))
        self.assertEqual(c.execute("select v from game_settings").fetchone()[0], "mgba")
        c.close()

    def test_diff_lists_changed_keys(self):
        sid = self.snap()["id"]
        self.ra.write_text('video_smooth = "true"\naudio_latency = "64"\nnew_key = "1"\n', encoding="utf-8")
        self.rmp.unlink()
        d = LC.diff(self.ud, self.sd, P, sid)
        ch = {c["path"]: c for c in d["changed"]}
        self.assertEqual(ch[".umrk/mlp1/retroarch/retroarch.cfg"]["keys"],
                         [{"key": "new_key", "old": None, "new": "1"}, {"key": "video_smooth", "old": "false", "new": "true"}])
        self.assertEqual([r["path"] for r in d["removed"]], [self.rmp.relative_to(self.sd).as_posix()])

    def test_restore_selected_groups_only_and_backs_up_first(self):
        sid = self.snap()["id"]
        self.ra.write_text('video_smooth = "true"\n', encoding="utf-8")
        self.opt.write_text('mgba_skip_bios = "ON"\n', encoding="utf-8")
        db_before = self.db.read_bytes()
        r = LC.restore(self.ud, self.sd, P, sid, ["retroarch"], running=lambda: [])
        self.assertTrue(r["success"], r)
        self.assertEqual(r["restored"], [".umrk/mlp1/retroarch/retroarch.cfg"])
        self.assertIn('video_smooth = "false"', self.ra.read_text(encoding="utf-8"))
        self.assertIn('"ON"', self.opt.read_text(encoding="utf-8"), "nhóm không chọn thì giữ nguyên")
        self.assertEqual(self.db.read_bytes(), db_before)
        self.assertIn(r["backup_before"], [s["id"] for s in LC.list_snapshots(self.ud)])

    def test_restore_refused_while_game_running_and_db_never_restorable(self):
        sid = self.snap()["id"]
        self.ra.write_text("x = 1\n", encoding="utf-8")
        r = LC.restore(self.ud, self.sd, P, sid, ["retroarch"], running=lambda: ["retroarch"])
        self.assertEqual((r["success"], r["error_kind"]), (False, "game_running"))
        self.assertEqual(self.ra.read_text(encoding="utf-8"), "x = 1\n")
        with self.assertRaises(LC.ConfigError):
            LC.restore(self.ud, self.sd, P, sid, ["leaf_db"], running=lambda: [])

    def test_prune_keeps_newest_and_drops_unused_objects(self):
        for i in range(4):
            self.ra.write_text(f'v = "{i}"\n', encoding="utf-8")
            self.snap(force=True, keep=2)
        self.assertEqual(len(LC.list_snapshots(self.ud)), 2)
        used = set()
        for s in LC.list_snapshots(self.ud):
            used |= {f["sha256"] for f in LC._manifest(self.ud, s["id"])["files"].values()}
        objs = {p.name for p in (self.ud / "ConfigVault" / "objects").rglob("*") if p.is_file()}
        self.assertEqual(objs, used)


if __name__ == "__main__":
    unittest.main()
