"""Xoá ROM nhiều game: CLI `rom delete PATH [PATH ...]` và logic màn Xoá ROM (không cần SDL).

Mọi test chạy trên thẻ giả trong thư mục tạm. Jawaka được thay bằng mock để đếm số lần rescan.
Chứng minh test biết đỏ: làm hỏng có chủ đích (rescan trong vòng lặp từng game, bỏ bước gộp trùng của
plan_rom_batch, để after_rom_delete đặt self.inv = None / pop stack) rồi chạy lại: các test tương ứng phải đỏ.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.cli as CLI
import engine.jawaka as jawaka
from engine import ui_screens as ui
from engine.inventory import InventoryEngine
from engine.probe import PlatformProbe

GAME = b"GBA-ROM-" + bytes(range(256)) * 4
ART = b"\x89PNG-art-" + bytes(range(64))
SAVE = b"SAVE-DATA"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_romdel_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir(parents=True)
        self.ud.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.req = mock.Mock(return_value={"ok": True, "message": "Quét xong.", "waited_s": 0.1})
        for p in (mock.patch.object(jawaka, "rescan_available", lambda *a, **k: {"available": True, "reason": ""}),
                  mock.patch.object(jawaka, "request_rescan", self.req)):
            p.start()
            self.addCleanup(p.stop)

    def w(self, path, data=GAME):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def cue_game(self, card, name):
        """Game đĩa: <name>.cue + <name>.bin."""
        cue = self.w(card / f"Roms/PSX/{name}.cue", f'FILE "{name}.bin" BINARY\n  TRACK 01 MODE2/2352\n'.encode())
        return cue, self.w(card / f"Roms/PSX/{name}.bin", b"BIN" * 500)

    def ledger(self):
        f = self.ud / "deletion-ledger.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


class TestCliMulti(Base):
    def run_cli(self, *argv):
        base = ["--quiet", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
            code = CLI.main(base + list(argv))
        return code, json.loads(out.getvalue())

    def three_games(self):
        a = self.w(self.sd1 / "Roms/GBA/Alpha.gba")
        b = self.w(self.sd2 / "Roms/GBA/Beta.gba", GAME * 2)
        art = self.w(self.sd1 / "Images/GBA/Alpha.png", ART)
        keep = self.w(self.sd1 / "Roms/GBA/Gamma.gba")
        self.w(self.sd1 / "Saves/GBA/Alpha.srm", SAVE)
        return a, b, art, keep

    def test_01_deletes_each_game_and_contract_json(self):
        a, b, art, keep = self.three_games()
        code, d = self.run_cli("rom", "delete", str(a), str(b), "--yes")
        self.assertEqual(code, 0, d)
        self.assertFalse(a.exists() or b.exists() or art.exists())
        self.assertTrue(keep.exists(), "game không được chọn phải còn")
        self.assertTrue((self.sd1 / "Saves/GBA/Alpha.srm").exists(), "save phải được giữ")
        self.assertEqual([g["path"] for g in d["games"]], [str(a), str(b)])
        self.assertEqual(len(d["games"][0]["deleted"]), 2, "ROM + ảnh bìa")
        self.assertEqual(len(d["games"][1]["deleted"]), 1)
        self.assertEqual(sorted(d["deleted"]), sorted(x for g in d["games"] for x in g["deleted"]))
        self.assertEqual(d["bytes_freed"], sum(g["bytes_freed"] for g in d["games"]))
        self.assertEqual(d["bytes_freed"], len(GAME) * 3 + len(ART))
        self.assertEqual((d["refused"], d["success"]), ([], True))
        self.assertTrue(d["games"][0]["plan"]["ok"])
        self.assertEqual(len(d["games"][0]["plan"]["saves_kept"]), 1)
        self.assertIn("rescan", d)

    def test_02_one_bad_game_does_not_block_the_others(self):
        a, b, _, _ = self.three_games()
        missing = self.sd1 / "Roms/GBA/KhongCo.gba"
        code, d = self.run_cli("rom", "delete", str(a), str(missing), str(b), "--yes")
        self.assertEqual(code, 1)
        self.assertFalse(a.exists() or b.exists())
        self.assertFalse(d["success"])
        self.assertEqual([x["path"] for x in d["refused"]], [str(missing)])
        self.assertIn("Từ chối", d["refused"][0]["reason"])
        self.assertEqual(d["games"][1]["deleted"], [])
        self.assertEqual(d["games"][1]["refused"], d["refused"])
        self.assertEqual(self.req.call_count, 1, "có xoá thật thì vẫn rescan")

    def test_03_without_yes_non_tty_deletes_nothing_even_for_many(self):
        a, b, art, _ = self.three_games()
        code, d = self.run_cli("rom", "delete", str(a), str(b))
        self.assertEqual(code, 1)
        self.assertTrue(a.exists() and b.exists() and art.exists())
        self.assertEqual((d["deleted"], d["success"], d["bytes_freed"]), ([], False, 0))
        self.assertIn("--yes", d["message"])
        self.assertEqual(len(d["games"]), 2)
        self.assertTrue(all(g["plan"]["ok"] for g in d["games"]), "native dùng đây để xem trước")
        self.assertEqual(self.ledger(), [])
        self.req.assert_not_called()

    def test_04_overlapping_selection_is_merged_not_deleted_twice(self):
        cue, binf = self.cue_game(self.sd1, "Disc")
        cue_again = Path(str(cue.parent) + "/../PSX/" + cue.name)   # cùng file, cách viết khác
        code, d = self.run_cli("rom", "delete", str(binf), str(cue), str(cue_again), "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual((d["refused"], d["success"]), ([], True))
        self.assertFalse(cue.exists() or binf.exists())
        self.assertEqual(len(d["games"]), 2, "đường dẫn lặp chỉ còn một mục")
        self.assertEqual(d["games"][0]["merged_into"], str(cue))
        self.assertEqual(sorted(d["deleted"]), sorted([str(cue.resolve()), str(binf.resolve())]))
        intents = [e["path"] for e in self.ledger() if e.get("status") == "intent"]
        self.assertEqual((len(intents), len(set(intents))), (2, 2), "mỗi file đúng một dòng sổ xoá")

    def test_05_two_overlapping_ok_plans_delete_shared_file_once(self):
        # m3u liệt kê 2 đĩa; chọn cả m3u lẫn .cue của đĩa 1: .bin của đĩa 1 nằm trong cả hai kế hoạch
        c1, b1 = self.cue_game(self.sd1, "D1")
        c2, b2 = self.cue_game(self.sd1, "D2")
        m3u = self.w(self.sd1 / "Roms/PSX/Multi.m3u", b"D1.cue\nD2.cue\n")
        code, d = self.run_cli("rom", "delete", str(c1), str(m3u), "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual(d["refused"], [], "xoá lần hai sẽ bị từ chối 'không phải file thường'")
        self.assertFalse(any(p.exists() for p in (c1, b1, c2, b2, m3u)))
        intents = [e["path"] for e in self.ledger() if e.get("status") == "intent"]
        self.assertEqual(len(intents), len(set(intents)))
        self.assertEqual(len(intents), 5)

    def test_06_rescan_exactly_once_for_the_whole_batch(self):
        a, b, _, keep = self.three_games()
        with mock.patch.object(CLI, "_auto_rescan", wraps=CLI._auto_rescan) as ar:
            code, d = self.run_cli("rom", "delete", str(a), str(b), str(keep), "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual(ar.call_count, 1)
        self.assertEqual(self.req.call_count, 1)

    def test_07_nothing_deleted_means_no_rescan(self):
        code, d = self.run_cli("rom", "delete", str(self.sd1 / "Roms/GBA/a.gba"), str(self.sd1 / "Roms/GBA/b.gba"), "--yes")
        self.assertEqual(code, 1)
        self.assertEqual((d["deleted"], d["success"]), ([], False))
        self.assertEqual(len(d["refused"]), 2)
        self.req.assert_not_called()

    def test_08_single_path_keeps_legacy_keys(self):
        a, _, _, _ = self.three_games()
        code, d = self.run_cli("rom", "delete", str(a), "--yes")
        self.assertEqual(code, 0, d)
        for k in ("plan", "deleted", "refused", "bytes_freed", "mode", "ledger_path", "rescan", "games", "success"):
            self.assertIn(k, d)
        self.assertTrue(d["plan"]["ok"])
        code, d = self.run_cli("rom", "delete", str(self.sd1 / "Roms/GBA/khong-co.gba"), "--yes")
        self.assertEqual(code, 1)
        self.assertFalse(d["ok"], "khoá cũ: chính plan hỏng")
        self.assertIn("Từ chối", d["message"])


class TestUiLogic(Base):
    def setUp(self):
        super().setUp()
        p = mock.patch.dict(os.environ, {ui.NO_RESCAN_ENV: "1"})
        p.start()
        self.addCleanup(p.stop)
        self.names = ["Alpha", "Bravo", "Charlie", "Delta"]
        self.files = {n: self.w(self.sd1 / f"Roms/GBA/{n}.gba") for n in self.names}
        self.art = {n: self.w(self.sd1 / f"Images/GBA/{n}.png", ART) for n in ("Bravo", "Charlie")}
        self.w(self.sd1 / "Roms/NES/Solo.nes")
        self.w(self.sd1 / "Saves/GBA/Bravo.srm", SAVE)
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        self.app = ui.App(probe, None, None, InventoryEngine(probe.primary_path, probe.secondary_path), None, self.ud, None)

    def enter(self, system="GBA"):
        self.app.open_rom_systems()
        self.app.wait_task()
        self.app.open_rom_games(system)
        self.depth = len(self.app.stack)
        return self.app.cur()

    def labels(self, scr):
        return [re.sub(r"\.(gba|nes)$", "", it.label) for it in scr.items]   # bỏ đuôi file cho gọn

    def goto(self, scr, name):
        idx = [i for i, l in enumerate(self.labels(scr)) if l.endswith(name)]
        self.assertTrue(idx, f"không thấy {name} trong {self.labels(scr)}")
        while scr.sel != idx[0]:
            self.app.on_action("DOWN" if scr.sel < idx[0] else "UP")

    def mark(self, scr, *names):
        for n in names:
            self.goto(scr, n)
            self.app.on_action("X")

    def confirm_delete(self):
        """A trên mục 'Xoá N game đã chọn' (hoặc game đơn) -> chờ kế hoạch -> chọn XOÁ -> chờ xoá xong."""
        self.app.on_action("A")
        self.app.wait_task()
        self.assertIsNotNone(self.app.dialog.on_confirm)
        self.assertEqual(self.app.dialog.choice, 0, "mặc định là Huỷ")
        self.app.on_action("RIGHT")
        self.app.on_action("A")
        self.app.wait_task()

    def inv_paths(self, sect="roms"):
        return {r["absolute_path"] for c in ("primary", "secondary") for r in (self.app.inv.get(c) or {}).get(sect, [])}

    def test_01_x_marks_and_header_and_batch_item(self):
        scr = self.enter()
        self.assertFalse(any(l.startswith("Xoá") for l in self.labels(scr)))
        self.mark(scr, "Bravo", "Delta")
        self.assertTrue(scr.items[0].label.startswith("Xoá 2 game đã chọn"))
        self.assertIn("Đã chọn 2 game", " ".join(scr.header))
        self.assertEqual([l for l in self.labels(scr) if l.startswith("[x]")], ["[x] Bravo", "[x] Delta"])
        self.assertTrue(self.labels(scr)[scr.sel].endswith("Delta"), "con trỏ ở lại trên game vừa bấm X dù danh sách lệch dòng")
        self.assertIn("[X] Bỏ chọn", scr.footer())
        self.app.on_action("X")   # bấm lại: bỏ chọn
        self.assertEqual(self.app.rom_marks["GBA"], {str(self.files["Bravo"])})

    def test_02_batch_delete_stays_put_and_updates_inventory(self):
        scr = self.enter()
        before = self.inv_paths()
        self.mark(scr, "Alpha", "Charlie")
        scr.sel = 0
        self.confirm_delete()
        self.assertIs(self.app.cur(), scr)
        self.assertEqual(len(self.app.stack), self.depth, "không bị pop về Library")
        self.assertIsNotNone(self.app.inv, "không được bỏ kết quả quét")
        self.assertEqual(self.app.dialog.title, "Kết quả xoá ROM")
        self.assertFalse(self.files["Alpha"].exists() or self.files["Charlie"].exists() or self.art["Charlie"].exists())
        self.assertEqual(self.inv_paths(), before - {str(self.files["Alpha"]), str(self.files["Charlie"])})
        self.assertNotIn(str(self.art["Charlie"]), self.inv_paths("images"))
        self.assertIn(str(self.art["Bravo"]), self.inv_paths("images"), "ảnh game khác phải còn")
        # Quy định đổi (đợt F): mục "Lọc: ..." luôn nằm đầu danh sách game -> kỳ vọng thêm nó ở đầu (viết lại, không bỏ).
        self.assertEqual(self.labels(scr), ["Lọc: tất cả", "[ ] Bravo", "[ ] Delta"])
        self.assertEqual(self.app.rom_marks["GBA"], set())
        self.assertEqual(self.labels(scr)[scr.sel], "[ ] Bravo", "con trỏ ở mục kế tiếp của game xoá đầu tiên")
        self.assertEqual(self.app.inv["total_roms"], len(self.inv_paths()))
        self.app.on_action("A")   # đóng hộp kết quả: vẫn ở màn game
        self.assertIsNone(self.app.dialog)
        self.assertIs(self.app.cur(), scr)

    def test_03_single_a_delete_also_stays_and_keeps_other_marks(self):
        scr = self.enter()
        self.mark(scr, "Delta")
        self.goto(scr, "Bravo")
        self.confirm_delete()
        self.assertIs(self.app.cur(), scr)
        self.assertEqual(len(self.app.stack), self.depth)
        self.assertTrue(self.files["Alpha"].exists() and not self.files["Bravo"].exists())
        self.assertTrue((self.sd1 / "Saves/GBA/Bravo.srm").exists(), "save phải được giữ")
        self.assertIn("[x] Delta", self.labels(scr), "đánh dấu của game còn lại giữ nguyên")
        self.assertTrue(self.labels(scr)[scr.sel].endswith("Charlie"), "con trỏ ở game kế tiếp")

    def test_04_system_list_counts_and_empty_system(self):
        scr = self.enter("NES")
        self.assertEqual(self.labels(scr), ["Lọc: tất cả", "[ ] Solo"])   # đợt F: mục Lọc luôn ở đầu
        scr.sel = 1                                                       # confirm_delete bấm A trên mục đang chọn: chọn game Solo
        self.confirm_delete()
        self.assertIs(self.app.cur(), scr)
        self.assertEqual(self.labels(scr), ["Lọc: tất cả", "Không còn game"])
        self.assertIn("Không còn game", scr.header[0])
        self.app.dialog = None
        self.app.on_action("B")
        systems = self.app.cur()
        # đợt F: mục đầu của màn hệ máy là "Game lớn nhất (mọi hệ)"
        self.assertEqual(self.labels(systems), ["Game lớn nhất (mọi hệ)", "GBA"], "hệ hết game biến khỏi danh sách")
        self.assertEqual(systems.items[1].tag, "4 file")

    def test_05_dup_cache_cleared_but_inventory_kept(self):
        scr = self.enter()
        self.app.dup_cache["auto"] = {"stale": True}
        self.mark(scr, "Delta")
        scr.sel = 0
        self.confirm_delete()
        self.assertEqual(self.app.dup_cache, {})
        self.assertIsNone(self.app.dup_compare)
        self.assertIn(str(self.files["Alpha"]), self.inv_paths())

    def test_06_cancel_deletes_nothing(self):
        scr = self.enter()
        self.mark(scr, "Alpha")
        scr.sel = 0
        self.app.on_action("A")
        self.app.wait_task()
        self.app.on_action("A")   # đang ở Huỷ
        self.assertIsNone(self.app.dialog)
        self.assertTrue(all(f.exists() for f in self.files.values()))
        self.assertEqual(self.app.rom_marks["GBA"], {str(self.files["Alpha"])})

    def test_07_prune_inventory_removes_only_the_deleted_paths(self):
        a, b, c = (self.w(self.tmp / n) for n in ("A.gba", "B.gba", "A.png"))
        inv = {"primary": {"roms": [{"absolute_path": str(a)}, {"absolute_path": str(b)}], "images": [{"absolute_path": str(c)}]},
               "secondary": None}
        self.assertEqual(ui.prune_inventory(inv, [str(a), str(c)]), 2)
        self.assertEqual([r["absolute_path"] for r in inv["primary"]["roms"]], [str(b)])
        self.assertEqual((inv["total_roms"], inv["total_images"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
