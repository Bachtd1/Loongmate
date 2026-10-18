"""
Tests đợt D (engine/cli.py + engine): file kế hoạch đã duyệt (--plan-out / --apply --plan) cho dedupe,
fix batch, move --bulk; các lệnh liệt kê chỉ đọc (snapshots, roms, bios md5, fix undo --check, update list,
agent status); đọc cue latin-1; id snapshot cùng giây đơn điệu; câu từ chối theo lệnh.

Mọi test gọi CLI.main([...]) thật trên thẻ giả trong thư mục tạm (stdin non-TTY), tái dùng Base của test_cli_wave_b.
Chứng minh test biết đỏ: đặt LOONGMATE_CLI_PATH tới bản engine/cli.py cố tình làm hỏng
(vd. bỏ kiểm plan_id, hoặc --plan quay về quét lại).
"""
import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from test_cli_wave_b import Base, CLI, GAME, MB  # noqa: E402
import engine.dedupe as dedupe_mod  # noqa: E402
import engine.savevault as sv_mod  # noqa: E402
from engine.autobackup import PidLock  # noqa: E402
from engine.savevault import SaveVault  # noqa: E402


def snap_tree(*roots):
    """{đường dẫn: (là thư mục, size, mtime_ns)} của mọi thứ dưới các thư mục — để chứng minh 'không ghi gì'."""
    out = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            st = p.stat()
            out[str(p)] = (p.is_dir(), 0 if p.is_dir() else st.st_size, st.st_mtime_ns)
    return out


class D(Base):
    def setUp(self):
        super().setUp()
        self.plan_path = self.tmp / "plan.json"   # NGOÀI thẻ và userdata

    def plan(self):
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def save_plan(self, doc):
        self.plan_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def touch_change(self, path, data):
        """Đổi nội dung và ép mtime khác hẳn (không phụ thuộc độ phân giải đồng hồ)."""
        st = Path(path).stat()
        Path(path).write_bytes(data)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


# ======================================================================== dedupe
class TestDedupePlan(D):
    def setUp(self):
        super().setUp()
        self.keep = self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.dup = self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.base = ["dedupe", "--prefer-card", "primary"]

    def make_plan(self, *extra):
        code, _, data = self.run_cli(*self.base, "--plan-out", str(self.plan_path), *extra)
        self.assertEqual(code, 0, data)
        return data

    def apply(self, *extra):
        return self.run_cli("dedupe", "--apply", "--plan", str(self.plan_path), "--yes", *extra)

    def test_01_plan_out_writes_schema_and_changes_nothing_on_cards(self):
        before = snap_tree(self.sd1, self.sd2)
        data = self.make_plan()
        self.assertEqual(snap_tree(self.sd1, self.sd2), before)
        doc = self.plan()
        self.assertEqual((doc["schema"], doc["kind"]), (1, "dedupe"))
        self.assertEqual(data["plan_id"], doc["plan_id"])
        want = hashlib.sha256(json.dumps(doc["items"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        self.assertEqual(doc["plan_id"], want)
        self.assertEqual(len(doc["items"]), 1)
        it = doc["items"][0]
        self.assertEqual(it["keep"]["path"], str(self.keep))
        self.assertEqual([r["path"] for r in it["remove"]], [str(self.dup)])
        self.assertEqual(it["sha256"], hashlib.sha256(GAME).hexdigest())
        self.assertRegex(doc["created_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_02_apply_plan_does_exactly_the_plan(self):
        self.make_plan()
        code, _, data = self.apply()
        self.assertEqual(code, 0, data)
        self.assertTrue(self.keep.exists())
        self.assertFalse(self.dup.exists())
        self.assertEqual(data["plan_id"], self.plan()["plan_id"])
        self.assertEqual(data["applied"], [{"keep": str(self.keep), "path": str(self.dup)}])
        self.assertEqual(data["refused"], [])
        self.assertEqual(self.req.call_count, 1)

    def test_03_apply_plan_never_rescans(self):
        """Nếu --plan quay về quét/băm lại (find_duplicates / scan_all) thì test này ĐỎ."""
        self.make_plan()
        with mock.patch.object(dedupe_mod, "find_duplicates", side_effect=AssertionError("không được quét lại")), \
                mock.patch.object(CLI.InventoryEngine, "scan_all", side_effect=AssertionError("không được quét lại")):
            code, _, data = self.apply()
        self.assertEqual(code, 0, data)
        self.assertFalse(self.dup.exists())

    def test_04_tampered_items_rejects_whole_plan_exit_3(self):
        self.make_plan()
        doc = self.plan()
        doc["items"][0]["remove"][0]["path"] = str(self.keep)       # đổi 1 chi tiết: định xoá luôn bản giữ
        self.save_plan(doc)
        code, _, data = self.apply()
        self.assertEqual(code, 3)
        self.assertFalse(data["success"])
        self.assertIn("plan_id", data["message"])
        self.assertTrue(self.keep.exists() and self.dup.exists())

    def test_05_one_byte_flip_in_file_is_caught(self):
        self.make_plan()
        raw = self.plan_path.read_bytes()
        i = raw.index(hashlib.sha256(GAME).hexdigest().encode()) + 3
        self.plan_path.write_bytes(raw[:i] + (b"0" if raw[i:i + 1] != b"0" else b"1") + raw[i + 1:])
        code, _, data = self.apply()
        self.assertEqual(code, 3, data)
        self.assertTrue(self.dup.exists())

    def test_06_wrong_kind_schema_age_future_all_exit_3(self):
        self.make_plan()
        good = self.plan()
        for field, val in (("kind", "batchfix"), ("schema", 2), ("schema", True),
                           ("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 25 * 3600))),
                           ("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600))),
                           ("created_at", "hôm qua")):
            doc = dict(good, **{field: val})
            self.save_plan(doc)
            code, _, data = self.apply()
            self.assertEqual(code, 3, (field, val, data))
            self.assertTrue(self.dup.exists(), (field, val))
        self.save_plan(dict(good, created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 23 * 3600))))
        self.assertEqual(self.apply()[0], 0)           # 23 giờ vẫn còn hạn

    def test_07_garbage_or_missing_file_exit_3(self):
        self.plan_path.write_text("{không phải json", encoding="utf-8")
        self.assertEqual(self.apply()[0], 3)
        self.plan_path.unlink()
        self.assertEqual(self.apply()[0], 3)
        self.assertTrue(self.dup.exists())

    def test_08_file_changed_after_plan_only_that_item_refused(self):
        other_keep = self.w(self.sd1 / "Roms/GBA/Other.gba", b"OTHER" * 50)
        other_dup = self.w(self.sd2 / "Roms/GBA/Other.gba", b"OTHER" * 50)
        self.make_plan()
        self.assertEqual(len(self.plan()["items"]), 2)
        self.touch_change(self.dup, GAME[:-1] + b"X")        # bản định xoá của game 1 đổi nội dung
        code, _, data = self.apply()
        self.assertEqual(code, 1, data)
        self.assertTrue(self.dup.exists(), "bản đã đổi nội dung KHÔNG được xoá")
        self.assertFalse(other_dup.exists(), "item còn hợp lệ vẫn phải làm")
        self.assertTrue(other_keep.exists())
        self.assertEqual([x["path"] for x in data["applied"]], [str(other_dup)])
        self.assertEqual([x["path"] for x in data["refused"]], [str(self.dup)])
        self.assertIn("SHA-256", data["refused"][0]["reason"])

    def test_09_kept_copy_changed_refuses_item(self):
        self.make_plan()
        self.touch_change(self.keep, GAME[:-1] + b"Z")
        code, _, data = self.apply()
        self.assertEqual(code, 1)
        self.assertTrue(self.dup.exists())
        self.assertEqual(data["applied"], [])
        self.assertIn("đã thay đổi", data["refused"][0]["reason"])

    def test_10_save_appearing_after_plan_protects_the_copy(self):
        self.make_plan()
        self.w(self.sd2 / "Saves/GBA/Game.srm", b"SAVE")      # save xuất hiện cho bản định xoá
        code, _, data = self.apply()
        self.assertEqual(code, 1)
        self.assertTrue(self.dup.exists())
        self.assertIn("save", data["refused"][0]["reason"])

    def test_11_plan_never_deletes_a_file_that_became_duplicate_later(self):
        """File mới thành bản trùng SAU khi lập kế hoạch không bao giờ bị xoá. Đỏ nếu --plan tự quét thêm."""
        self.make_plan()
        late = self.w(self.sd2 / "Roms/GBA/Late.gba")         # cùng nội dung, KHÔNG có trong kế hoạch
        late2 = self.w(self.sd1 / "Roms/GBA/Sub/Late2.gba")
        code, _, data = self.apply()
        self.assertEqual(code, 0, data)
        self.assertFalse(self.dup.exists())
        self.assertTrue(late.exists() and late2.exists() and self.keep.exists())
        self.assertEqual([x["path"] for x in data["applied"]], [str(self.dup)])

    def test_12_flags_combination_errors_exit_2(self):
        self.make_plan()
        self.assertEqual(self.run_cli("dedupe", "--apply", "--plan-out", str(self.plan_path), "--yes")[0], 2)
        self.assertEqual(self.run_cli("dedupe", "--plan", str(self.plan_path))[0], 2)
        self.assertEqual(self.run_cli("dedupe", "--plan-out", str(self.plan_path), "--compare")[0], 2)
        self.assertTrue(self.dup.exists())

    def test_13_without_yes_noninteractive_refused_and_nothing_deleted(self):
        self.make_plan()
        code, _, data = self.run_cli("dedupe", "--apply", "--plan", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("--yes", data["message"])
        self.assertTrue(self.dup.exists())

    def test_14_quarantine_flag_must_match_plan(self):
        self.make_plan()
        code, _, data = self.apply("--quarantine")
        self.assertEqual(code, 3, data)
        self.assertTrue(self.dup.exists())
        self.make_plan("--quarantine")
        code, _, data = self.apply()                          # kế hoạch đã duyệt quarantine -> làm quarantine
        self.assertEqual(code, 0, data)
        self.assertEqual(data["mode"], "quarantine")
        self.assertFalse(self.dup.exists())
        self.assertTrue(list((self.ud / "Quarantine").rglob("Game.gba")) or list(self.sd2.rglob("Quarantine/**/Game.gba")))

    def test_15_old_apply_without_plan_unchanged(self):
        code, _, data = self.run_cli(*self.base, "--apply", "--yes")
        self.assertEqual(code, 0, data)
        self.assertIs(data["applied"], True)
        self.assertFalse(self.dup.exists())

    def test_16_empty_plan_is_ok_and_does_nothing(self):
        self.dup.unlink()
        self.make_plan()
        self.assertEqual(self.plan()["items"], [])
        code, _, data = self.apply()
        self.assertEqual((code, data["applied"], data["refused"]), (0, [], []))


# ======================================================================== fix batch
CUE = b'FILE "Wrong.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'


class TestFixPlan(D):
    def setUp(self):
        super().setUp()
        self.cue = self.w(self.sd1 / "Roms/PSX/Game.cue", CUE)
        self.w(self.sd1 / "Roms/PSX/Game.bin", b"BIN" * 100)
        self.apply_args = ("fix", "batch", "--apply", "--plan", str(self.plan_path), "--yes")

    def make_plan(self):
        code, _, data = self.run_cli("fix", "batch", "--plan-out", str(self.plan_path))
        self.assertEqual(code, 0, data)
        return data

    def test_01_plan_out_and_apply_fix_cue(self):
        before = snap_tree(self.sd1, self.sd2)
        data = self.make_plan()
        self.assertEqual(snap_tree(self.sd1, self.sd2), before)
        doc = self.plan()
        self.assertEqual((doc["kind"], doc["schema"]), ("batchfix", 1))
        self.assertEqual(data["plan_id"], doc["plan_id"])
        cue_steps = [s for s in doc["items"] if s["kind"] == "fix_cue"]
        self.assertEqual(len(cue_steps), 1)
        self.assertEqual(cue_steps[0]["data"]["sha256_before"], hashlib.sha256(CUE).hexdigest())
        code, _, res = self.run_cli(*self.apply_args)
        self.assertEqual(code, 0, res)
        self.assertEqual(res["plan_id"], doc["plan_id"])
        self.assertIn(b'"Game.bin"', self.cue.read_bytes())
        self.assertTrue(any(d["kind"] == "fix_cue" for d in res["applied"]))
        self.assertEqual(res["refused"], [])

    def test_02_tampered_plan_rejected_exit_3(self):
        self.make_plan()
        doc = self.plan()
        doc["items"][0]["label"] = "đã bị sửa"
        self.save_plan(doc)
        code, _, data = self.run_cli(*self.apply_args)
        self.assertEqual(code, 3)
        self.assertIn("plan_id", data["message"])
        self.assertEqual(self.cue.read_bytes(), CUE)

    def test_03_cue_changed_after_plan_is_refused(self):
        self.make_plan()
        self.w(self.cue, CUE + b"REM nguoi dung sua tay\r\n")
        code, _, res = self.run_cli(*self.apply_args)
        self.assertEqual(code, 1, res)
        self.assertNotIn(b'"Game.bin"', self.cue.read_bytes())
        self.assertTrue(any(x["kind"] == "fix_cue" and "đã đổi" in x["reason"] for x in res["refused"]))

    def test_04_legacy_folder_moves_only_listed_unchanged_files(self):
        a = self.w(self.sd1 / "Roms/PS/a.chd", b"AAA")
        b = self.w(self.sd1 / "Roms/PS/b.chd", b"BBB")
        self.make_plan()
        listed = next(s for s in self.plan()["items"] if s["kind"] == "legacy_folder" and s["data"]["section"] == "Roms")
        self.assertEqual(sorted(f[0] for f in listed["data"]["files"]), ["a.chd", "b.chd"])
        new = self.w(self.sd1 / "Roms/PS/new.chd", b"NEW")           # xuất hiện sau khi duyệt
        self.touch_change(b, b"BB2")                                  # đổi sau khi duyệt
        code, _, res = self.run_cli(*self.apply_args)
        self.assertEqual(code, 1, res)
        self.assertEqual((self.sd1 / "Roms/PSX/a.chd").read_bytes(), b"AAA")
        self.assertFalse(a.exists())
        self.assertTrue(new.exists() and not (self.sd1 / "Roms/PSX/new.chd").exists(), "file ngoài kế hoạch phải đứng yên")
        self.assertTrue(b.exists() and not (self.sd1 / "Roms/PSX/b.chd").exists())
        self.assertTrue((self.sd1 / "Roms/PS").is_dir())
        why = " ".join(x["reason"] for x in res["refused"])
        self.assertIn("kế hoạch", why)

    def test_05_flag_errors_exit_2(self):
        self.make_plan()
        self.assertEqual(self.run_cli("fix", "batch", "--apply", "--plan", str(self.plan_path), "--only", "fix_cue", "--yes")[0], 2)
        self.assertEqual(self.run_cli("fix", "batch", "--plan", str(self.plan_path))[0], 2)
        self.assertEqual(self.run_cli("fix", "batch", "--apply", "--plan-out", str(self.plan_path), "--yes")[0], 2)

    def test_06_only_filter_is_recorded_in_plan(self):
        self.w(self.sd1 / "Roms/PS/a.chd", b"AAA")
        code, _, _ = self.run_cli("fix", "batch", "--only", "fix_cue", "--plan-out", str(self.plan_path))
        self.assertEqual(code, 0)
        self.assertEqual({s["kind"] for s in self.plan()["items"]}, {"fix_cue"})
        self.assertEqual(self.plan()["params"]["only"], ["fix_cue"])

    def test_07_undo_check_reports_batch_without_touching_anything(self):
        code, _, none = self.run_cli("fix", "undo", "--check")
        self.assertEqual((code, none["has_batch"], none["operations"]), (0, False, 0))
        self.make_plan()
        self.run_cli(*self.apply_args)
        journal = self.ud / "batchfix-journal.jsonl"
        before = snap_tree(self.sd1, self.sd2, self.ud)
        code, _, chk = self.run_cli("fix", "undo", "--check")
        self.assertEqual(snap_tree(self.sd1, self.sd2, self.ud), before)
        self.assertEqual(code, 0)
        self.assertTrue(chk["has_batch"])
        self.assertGreaterEqual(chk["operations"], 1)
        self.assertEqual(chk["by_op"].get("fix_cue"), 1)
        self.assertTrue(chk["batch_id"] and chk["created_at"])
        self.assertTrue(journal.exists())
        self.run_cli("fix", "undo", "--yes")
        code, _, after = self.run_cli("fix", "undo", "--check")
        self.assertFalse(after["has_batch"])

    def test_08_undo_refusal_message_is_not_about_deleting(self):
        code, _, data = self.run_cli("fix", "undo")
        self.assertEqual(code, 1)
        self.assertNotIn("xoá gì", data["message"])
        self.assertIn("Không hoàn tác gì", data["message"])


# ======================================================================== move
class TestMovePlan(D):
    def setUp(self):
        super().setUp()
        self.free["sd1"] = 100 * MB
        self.roms = [self.w(self.sd1 / f"Roms/GBA/G{i}.gba", b"x" * MB + bytes([i])) for i in range(3)]
        self.bulk = ["move", "--bulk", "--to", "secondary", "--target-free-mb", "102"]   # cần 2 game

    def make_plan(self):
        code, _, data = self.run_cli(*self.bulk, "--plan-out", str(self.plan_path))
        self.assertEqual(code, 0, data)
        return data

    def apply(self):
        return self.run_cli("move", "--to", "secondary", "--apply", "--plan", str(self.plan_path), "--yes")

    def planned_names(self):
        return [Path(i["rom_path"]).name for i in self.plan()["items"]]

    def test_01_plan_out_then_apply_moves_exactly_planned_games(self):
        before = snap_tree(self.sd1, self.sd2)
        data = self.make_plan()
        self.assertEqual(snap_tree(self.sd1, self.sd2), before)
        doc = self.plan()
        self.assertEqual((doc["kind"], data["plan_id"]), ("move", doc["plan_id"]))
        names = self.planned_names()
        self.assertEqual(len(names), 2)
        self.assertTrue(all("mtime_ns" in f for i in doc["items"] for f in i["plan"]["files"]))
        code, _, res = self.apply()
        self.assertEqual(code, 0, res)
        self.assertEqual(sorted(a["game"] for a in res["applied"]), sorted(names))
        for r in self.roms:
            self.assertEqual((self.sd2 / "Roms/GBA" / r.name).exists(), r.name in names)
            self.assertEqual(r.exists(), r.name not in names)
        self.assertEqual(res["plan_id"], doc["plan_id"])

    def test_02_game_changed_after_plan_refused_others_still_move(self):
        self.make_plan()
        names = self.planned_names()
        changed = self.sd1 / "Roms/GBA" / names[0]
        self.touch_change(changed, b"y" * MB + b"!")
        code, _, res = self.apply()
        self.assertEqual(code, 1, res)
        self.assertTrue(changed.exists() and not (self.sd2 / "Roms/GBA" / names[0]).exists())
        self.assertTrue((self.sd2 / "Roms/GBA" / names[1]).exists())
        self.assertEqual(len(res["applied"]), 1)
        self.assertIn("đã đổi", res["refused"][0]["reason"])

    def test_03_unplanned_game_is_never_moved(self):
        self.make_plan()
        names = self.planned_names()
        left = next(r for r in self.roms if r.name not in names)
        self.apply()
        self.assertTrue(left.exists() and not (self.sd2 / "Roms/GBA" / left.name).exists())

    def test_04_tamper_and_dest_mismatch_exit_3(self):
        self.make_plan()
        doc = self.plan()
        doc["items"][0]["plan"]["files"][0]["dst"] = str(self.sd2 / "Roms/GBA/evil.gba")
        self.save_plan(doc)
        code, _, data = self.apply()
        self.assertEqual(code, 3)
        self.assertIn("plan_id", data["message"])
        self.make_plan()
        code, _, data = self.run_cli("move", "--to", "primary", "--apply", "--plan", str(self.plan_path), "--yes")
        self.assertEqual(code, 3, data)
        self.assertTrue(all(r.exists() for r in self.roms))

    def test_05_single_game_plan(self):
        code, _, data = self.run_cli("move", str(self.roms[0]), "--to", "secondary", "--plan-out", str(self.plan_path))
        self.assertEqual(code, 0, data)
        self.assertEqual(len(self.plan()["items"]), 1)
        self.assertEqual(self.plan()["items"][0]["system"], "GBA")
        code, _, res = self.apply()
        self.assertEqual(code, 0, res)
        self.assertTrue((self.sd2 / "Roms/GBA/G0.gba").exists() and not self.roms[0].exists())

    def test_06_apply_plan_never_replans(self):
        self.make_plan()
        with mock.patch.object(CLI.transfer_mod, "plan_bulk_move", side_effect=AssertionError("không được lập lại kế hoạch")), \
                mock.patch.object(CLI.InventoryEngine, "scan_all", side_effect=AssertionError("không được quét lại")):
            code, _, res = self.apply()
        self.assertEqual(code, 0, res)

    def test_07_refusal_message_says_move_nothing(self):
        code, _, data = self.run_cli(*self.bulk, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Không chuyển gì", data["message"])
        self.assertNotIn("xoá gì", data["message"])


# ======================================================================== lệnh liệt kê chỉ đọc
class TestReadOnlyListings(D):
    def setUp(self):
        super().setUp()
        self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        self.w(self.sd1 / "Roms/PSX/Disc.chd", b"CHD" * 50)
        self.w(self.sd1 / "Saves/GBA/Game.srm", b"SAVE")
        self.w(self.sd1 / "States/GBA/Game.state1", b"STATE")
        self.w(self.sd1 / "BIOS/scph5501.bin", b"khong-phai-bios-that")
        self.run_cli("history")      # mồi: các thư mục userdata do main() dựng lần đầu đã có sẵn trước khi so sánh

    def readonly(self, *argv, **kw):
        before = snap_tree(self.sd1, self.sd2, self.ud)
        out = self.run_cli(*argv, **kw)
        self.assertEqual(snap_tree(self.sd1, self.sd2, self.ud), before, f"{argv} đã ghi file/thư mục")
        return out

    def test_01_snapshots_lists_all_and_writes_nothing(self):
        code, _, empty = self.readonly("snapshots")
        self.assertEqual((code, empty["count"], empty["snapshots"], empty["latest_id"]), (0, 0, [], None))
        vault = SaveVault(self.sd1, self.sd2, self.ud)
        ids = []
        with mock.patch.object(sv_mod.time, "strftime", side_effect=self._clock()):
            for _ in range(2):
                ids.append(vault.create_snapshot(label="manual")["snapshot_id"])
        code, _, data = self.readonly("snapshots")
        self.assertEqual(code, 0)
        self.assertEqual(data["count"], 2)
        self.assertEqual([s["id"] for s in data["snapshots"]], ids[::-1])     # mới nhất trước
        s = data["snapshots"][0]
        self.assertEqual(set(s), {"id", "label", "auto", "created_at", "total_files", "total_size_bytes", "path", "verified"})
        self.assertEqual((s["label"], s["auto"], s["verified"]), ("manual", False, None))
        self.assertGreaterEqual(s["total_files"], 2)
        self.assertIsNone(data["vault_verify"])

    def _clock(self):
        n = {"i": 0}
        real = sv_mod.time.strftime

        def fake(fmt, *a):
            if fmt == "%Y%m%d_%H%M%S":
                n["i"] += 1
                return f"20260101_00{n['i'] // 60:02d}{n['i'] % 60:02d}"
            return real(fmt, *a)
        return fake

    def test_02_snapshots_verified_only_when_full_ok_verify_covers_it(self):
        SaveVault(self.sd1, self.sd2, self.ud).create_snapshot(label="auto")
        state = self.ud / "agent_state.json"
        now = time.time()
        for vv, want in (({"ok": True, "ts": now + 100, "partial": False}, True),
                         ({"ok": True, "ts": now - 86400, "partial": False}, None),   # snapshot mới hơn lần kiểm
                         ({"ok": True, "ts": now + 100, "partial": True}, None),
                         ({"ok": False, "ts": now + 100, "partial": False}, None)):
            state.write_text(json.dumps({"vault_verify": vv}), encoding="utf-8")
            _, _, data = self.readonly("snapshots")
            self.assertEqual(data["snapshots"][0]["verified"], want, vv)
            self.assertEqual(data["vault_verify"], vv)
        self.assertTrue(data["snapshots"][0]["auto"])

    def test_03_roms_fields_filters_and_writes_nothing(self):
        code, _, data = self.readonly("roms")
        self.assertEqual(code, 0)
        by = {(r["card"], r["name"]): r for r in data["roms"]}
        g1 = by[("primary", "Game.gba")]
        self.assertEqual(set(g1), {"path", "name", "system", "card", "size", "extension", "has_save", "has_state"})
        self.assertEqual((g1["system"], g1["size"], g1["extension"]), ("GBA", len(GAME), ".gba"))
        self.assertEqual(g1["path"], str(self.sd1.resolve() / "Roms/GBA/Game.gba"))
        self.assertTrue(g1["has_save"] and g1["has_state"])
        g2 = by[("secondary", "Game.gba")]
        self.assertTrue(g2["has_save"], "save ở thẻ kia vẫn tính (cùng tên)")
        self.assertFalse(by[("primary", "Disc.chd")]["has_save"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["total_bytes"], sum(r["size"] for r in data["roms"]))
        _, _, only2 = self.readonly("roms", "--card", "secondary")
        self.assertEqual([r["card"] for r in only2["roms"]], ["secondary"])
        _, _, ps = self.readonly("roms", "--system", "ps")          # alias không phân biệt hoa/thường
        self.assertEqual([r["name"] for r in ps["roms"]], ["Disc.chd"])
        self.assertEqual(ps["filters"], {"system": "ps", "card": None})

    def test_03b_roms_skips_cover_images_in_imgs_folder(self):
        """Leaf để ảnh bìa arcade trong Roms/<hệ>/Imgs/ — không phải game, không được hiện để xoá."""
        img = self.sd1 / "Roms/GBA/Imgs/Game.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        img.write_bytes(b"PNG-fake")
        _, _, data = self.readonly("roms")
        self.assertNotIn(str(img.resolve()), [r["path"] for r in data["roms"]])
        self.assertEqual(data["count"], 3)

    def test_04_bios_has_expected_md5_for_present_and_missing(self):
        code, _, data = self.readonly("bios")
        self.assertEqual(code, 0)
        psx = next(s for s in data["systems"] if s["system"] == "PSX")
        rows = {r["name"]: r for r in psx["required"]}
        self.assertEqual(rows["scph5501.bin"]["status"], "wrong_hash")
        self.assertEqual(rows["scph5501.bin"]["expected_md5"], ["490f666e1afb15b7362b406ed1cea246"])
        self.assertEqual(rows["scph5500.bin"]["status"], "missing")
        self.assertEqual(rows["scph5500.bin"]["expected_md5"], ["8dd7d5296a650fac7319bce665a6a53c"])
        self.assertTrue(all(isinstance(r["expected_md5"], list) for s in data["systems"] for r in s["required"]))

    def test_05_update_list_reads_manifests_installed_flag_and_verify_is_opt_in(self):
        upd = self.sd1 / "LoongMate-update"
        good_file = b"print('hi')\n"
        sha = hashlib.sha256(good_file).hexdigest()
        self.w(upd / "v1/LoongMate.pak/engine/x.py", good_file)
        self.w(upd / "v1/manifest.json", json.dumps({"schema": 1, "version": "1.0", "created_at": "2026-10-01 10:00:00",
                                                     "files": {"LoongMate.pak/engine/x.py": sha}}).encode())
        self.w(upd / "v2/LoongMate.pak/engine/x.py", b"tampered")
        self.w(upd / "v2/manifest.json", json.dumps({"schema": 1, "version": "2.0", "created_at": "2026-10-02 10:00:00",
                                                     "files": {"LoongMate.pak/engine/x.py": sha}}).encode())
        self.w(upd / "v3/manifest.json", b"{hong")
        self.w(self.ud / "installed-version.json", json.dumps({"schema": 1, "version": "1.0",
                                                               "bundle_created_at": "2026-10-01 10:00:00"}).encode())
        code, _, data = self.readonly("update", "list")
        self.assertEqual(code, 0)
        by = {b["name"]: b for b in data["bundles"]}
        self.assertEqual(data["count"], 3)
        self.assertEqual((by["v1"]["version"], by["v1"]["created_at"], by["v1"]["files"], by["v1"]["installed"]),
                         ("1.0", "2026-10-01 10:00:00", 1, True))
        self.assertFalse(by["v2"]["installed"])
        self.assertIsNone(by["v1"]["verify"])
        self.assertFalse(data["verified"])
        self.assertIn("manifest", by["v3"]["error"])
        self.assertEqual(data["installed"]["version"], "1.0")
        code, _, ver = self.readonly("update", "list", "--verify")
        by = {b["name"]: b for b in ver["bundles"]}
        self.assertTrue(by["v1"]["verify"]["ok"])
        self.assertFalse(by["v2"]["verify"]["ok"])
        self.assertTrue(by["v2"]["verify"]["errors"])
        self.assertIsNone(by["v3"]["verify"])

    def test_06_update_list_empty(self):
        code, _, data = self.readonly("update", "list")
        self.assertEqual((code, data["count"], data["bundles"], data["installed"]), (0, 0, [], None))

    def test_07_agent_status_never_stopped_running(self):
        code, _, st = self.readonly("agent", "status")
        self.assertEqual((code, st["state"], st["running"], st["pid"]), (0, "never", False, None))
        self.assertFalse((self.ud / "agent.lock").exists(), "lệnh trạng thái không được tạo agent.lock")
        for k in ("last_sample_ts", "last_backup", "vault_verify", "prewarm", "reload"):
            self.assertIsNone(st[k], k)
        vv = {"ok": True, "ts": 1790000000, "partial": False, "checked": 12}
        pw = {"ts": 1790000100, "hashed": 3, "remaining": 0, "done": True}
        lb = {"at": "2026-10-02 10:00:00", "success": True, "id": "snap_x"}
        (self.ud / "agent_state.json").write_text(json.dumps({"vault_verify": vv, "prewarm": pw, "last_backup": lb}), encoding="utf-8")
        rl = {"from": "aaa1111", "to": "bbb2222", "ts": 1790000200}
        (self.ud / "agent_state.json").write_text(json.dumps({"vault_verify": vv, "prewarm": pw, "last_backup": lb,
                                                              "reload": rl}), encoding="utf-8")
        code, _, st = self.readonly("agent", "status")
        self.assertEqual(st["state"], "stopped")
        self.assertEqual((st["vault_verify"], st["prewarm"], st["last_backup"]), (vv, pw, lb))
        self.assertEqual(st["reload"], rl)
        self.assertFalse((self.ud / "agent.lock").exists())

    def test_08_agent_status_lock_file_without_state_is_stopped_not_never(self):
        (self.ud / "agent.lock").write_bytes(b"4242")
        _, _, st = self.readonly("agent", "status")
        self.assertEqual(st["state"], "stopped")

    @unittest.skipIf(os.name == "nt", "flock chỉ có trên Linux")
    def test_09_agent_status_running_when_lock_held(self):
        lock = PidLock(self.ud / "agent.lock")
        self.assertTrue(lock.acquire())
        try:
            before = (self.ud / "agent.lock").read_bytes()
            _, _, st = self.run_cli("agent", "status")
            self.assertEqual(st["state"], "running")
            self.assertEqual(st["pid"], os.getpid())
            self.assertEqual((self.ud / "agent.lock").read_bytes(), before)
        finally:
            lock.release()

    def test_10_plan_out_preview_writes_only_the_plan_file(self):
        """dedupe/fix/move xem trước với --plan-out: ngoài file kế hoạch, thẻ và userdata (trừ cache băm) không đổi."""
        before = snap_tree(self.sd1, self.sd2)
        for argv in (("dedupe",), ("fix", "batch"), ("move", "--bulk", "--to", "secondary", "--target-free-mb", "1")):
            self.run_cli(*argv, "--plan-out", str(self.plan_path))
            self.assertTrue(self.plan_path.exists(), argv)
            self.plan_path.unlink()
        self.assertEqual(snap_tree(self.sd1, self.sd2), before)


# ======================================================================== lỗi nhỏ
class TestCueLatin1(D):
    def setUp(self):
        super().setUp()
        self.cue_bytes = 'FILE "Pokémon.bin" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'.encode("latin-1")
        self.cue = self.w(self.sd1 / "Roms/PSX/Pokémon.cue", self.cue_bytes)
        self.bin = self.w(self.sd1 / "Roms/PSX/Pokémon.bin", b"BIN" * 100)   # tên utf-8 trên đĩa

    def test_01_doctor_does_not_report_a_latin1_track_name_as_missing(self):
        _, _, data = self.run_cli("scan")      # mã thoát 3 còn do thẻ giả không phải FAT32 -> không dùng làm tiêu chí
        ids = [i["id"] for i in data["issues"]]
        self.assertFalse([i for i in ids if "CUE" in i and "Pok" in i], ids)

    def test_02_dedupe_read_refs_decodes_latin1(self):
        self.assertEqual([p.name for p in dedupe_mod._read_refs(self.cue)], ["Pokémon.bin"])

    def test_03_utf8_and_bom_cues_still_work(self):
        u = self.w(self.sd1 / "Roms/PSX/Ü.cue", b'\xef\xbb\xbfFILE "Pok\xc3\xa9mon.bin" BINARY\r\n')
        self.assertEqual([p.name for p in dedupe_mod._read_refs(u)], ["Pokémon.bin"])


class TestSnapshotIdMonotonic(unittest.TestCase):
    def test_same_second_suffix_never_reuses_a_pruned_one(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="loongmate_sv_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sd, ud = tmp / "sd", tmp / "ud"
        (sd / "Saves").mkdir(parents=True)
        ud.mkdir()
        (sd / "Saves" / "g.srm").write_bytes(b"x")
        vault = SaveVault(sd, None, ud)          # max_retention = 5
        real = sv_mod.time.strftime
        fake = lambda fmt, *a: "20260101_000000" if fmt == "%Y%m%d_%H%M%S" else real(fmt, *a)   # noqa: E731
        ids = []
        with mock.patch.object(sv_mod.time, "strftime", side_effect=fake):
            for i in range(9):
                (sd / "Saves" / "g.srm").write_bytes(b"x" * (i + 1))
                r = vault.create_snapshot(label="manual")
                self.assertTrue(r["success"], r)
                ids.append(r["snapshot_id"])
                self.assertEqual(vault.list_snapshots()[0]["id"], r["snapshot_id"], f"lần {i + 1}: snapshot mới phải là mới nhất")
                self.assertTrue((vault.snapshots_dir / r["snapshot_id"]).is_dir(), f"lần {i + 1}: snapshot mới bị prune")
        self.assertEqual(len(set(ids)), 9, ids)
        self.assertEqual(ids[0], "snap_20260101_000000_manual")            # định dạng id cũ không đổi
        self.assertEqual(ids[1], "snap_20260101_000000_manual_2")
        self.assertEqual([s["id"] for s in vault.list_snapshots()], ids[::-1][:5])

    def test_old_vault_with_gaps_still_reads_and_continues_above_max(self):
        import shutil
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="loongmate_sv_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sd, ud = tmp / "sd", tmp / "ud"
        (sd / "Saves").mkdir(parents=True)
        ud.mkdir()
        (sd / "Saves" / "g.srm").write_bytes(b"x")
        vault = SaveVault(sd, None, ud)
        for name in ("snap_20260101_000000_manual_7", "snap_20260101_000000_manual_3"):   # kho cũ: chỉ còn _3 và _7
            d = vault.snapshots_dir / name
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"snapshot_id": name, "committed": True, "created_at": "x"}))
        real = sv_mod.time.strftime
        fake = lambda fmt, *a: "20260101_000000" if fmt == "%Y%m%d_%H%M%S" else real(fmt, *a)   # noqa: E731
        with mock.patch.object(sv_mod.time, "strftime", side_effect=fake):
            r = vault.create_snapshot(label="manual")
        self.assertEqual(r["snapshot_id"], "snap_20260101_000000_manual_8")
        self.assertEqual(vault.list_snapshots()[0]["id"], r["snapshot_id"])


class TestRefusalWording(D):
    def test_update_apply_refusal_does_not_claim_nothing_deleted(self):
        upd = self.sd1 / "LoongMate-update"
        f = b"x"
        self.w(upd / "v1/LoongMate.pak/a.txt", f)
        self.w(upd / "v1/manifest.json", json.dumps({"schema": 1, "version": "1.0", "files": {
            "LoongMate.pak/a.txt": hashlib.sha256(f).hexdigest()}}).encode())
        apps = self.tmp / "apps"
        apps.mkdir()
        code, _, data = self.run_cli("update", "apply", "--apps-dir", str(apps))
        self.assertEqual(code, 1)
        self.assertIn("Không cập nhật gì", data["message"])
        self.assertNotIn("xoá gì", data["message"])
        code, _, data = self.run_cli("update", "rollback", "--apps-dir", str(apps))
        self.assertIn("Không đổi gì", data["message"])

    def test_dedupe_refusal_still_says_nothing_deleted(self):
        self.w(self.sd1 / "Roms/GBA/Game.gba")
        self.w(self.sd2 / "Roms/GBA/Game.gba")
        code, _, data = self.run_cli("dedupe", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("Không xoá gì", data["message"])


if __name__ == "__main__":
    unittest.main()
