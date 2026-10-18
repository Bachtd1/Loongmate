"""Đợt F: CLI khởi động nhẹ, `roms --sort/--save/--query/--limit`, màn Xoá ROM sắp xếp/lọc/"Game lớn nhất",
và khôi phục save theo từng game (`saves list|history|restore` + màn SaveVault).

Mọi test chạy hành vi thật trên thẻ giả trong thư mục tạm (không grep mã nguồn).
Chứng minh test biết đỏ (đã làm tay, kết quả ghi trong báo cáo): (1) restore_game_files gọi restore_snapshot
mà KHÔNG chọn file (ghi cả vault) -> test_restore_only_writes_that_game đỏ; (2) bỏ nhánh "không --yes thì chỉ xem
trước" -> test_restore_without_yes_writes_nothing đỏ; (3) thêm khoá "sort" vào filters khi không dùng cờ ->
test_roms_default_output_unchanged đỏ; (4) cli.py import ui_screens ở đầu -> test_import_time_* đỏ; (5) bỏ nâng hạn
mức giữ snapshot -> test_restore_oldest_snapshot_when_retention_full đỏ.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.cli as CLI
from engine import jawaka
from engine import savevault as sv_mod
from engine import ui_screens as ui
from engine.inventory import InventoryEngine
from engine.probe import PlatformProbe
from engine.savevault import SaveVault, game_of

ROM = b"ROM" * 100


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="loongmate_wavef_"))
        self.sd1, self.sd2, self.ud = self.tmp / "sd1", self.tmp / "sd2", self.tmp / "ud"
        for card in (self.sd1, self.sd2):
            for s in ("Roms", "Images", "Saves", "States", "BIOS"):
                (card / s).mkdir(parents=True)
        self.ud.mkdir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for p in (mock.patch.object(jawaka, "rescan_available", lambda *a, **k: {"available": True, "reason": ""}),
                  mock.patch.object(jawaka, "request_rescan", mock.Mock(return_value={"ok": True, "message": "ok", "waited_s": 0}))):
            p.start()
            self.addCleanup(p.stop)

    def w(self, path, data=ROM):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_cli(self, *argv, raw=False):
        base = ["--quiet", "--primary", str(self.sd1), "--secondary", str(self.sd2), "--userdata", str(self.ud), "--json"]
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
            code = CLI.main(base + list(argv))
        return code, (out.getvalue() if raw else json.loads(out.getvalue()))


# ---------------------------------------------------------------- CLI roms
class TestCliRoms(Base):
    def setUp(self):
        super().setUp()
        self.a = self.w(self.sd1 / "Roms/GBA/Alpha.gba", b"a" * 1000)
        self.b = self.w(self.sd1 / "Roms/GBA/Bravo.gba", b"b" * 3000)
        self.c = self.w(self.sd2 / "Roms/GBA/Charlie.gba", b"c" * 2000)
        self.v = self.w(self.sd1 / "Roms/NES/Đường Hầm Xuyên Việt.nes", b"v" * 500)
        self.w(self.sd1 / "Saves/GBA/Alpha.srm", b"s")
        self.w(self.sd2 / "Saves/GBA/CHARLIE.srm", b"s")           # khác hoa/thường, ở thẻ khác: vẫn tính là có save

    def names(self, d):
        return [r["name"] for r in d["roms"]]

    def test_roms_default_output_unchanged(self):
        code, d = self.run_cli("roms")
        self.assertEqual(code, 0)
        self.assertEqual(list(d), ["count", "total_bytes", "filters", "roms"])
        self.assertEqual(d["filters"], {"system": None, "card": None})
        self.assertEqual(self.names(d), ["Alpha.gba", "Bravo.gba", "Đường Hầm Xuyên Việt.nes", "Charlie.gba"])  # thẻ, hệ, tên
        self.assertEqual((d["count"], d["total_bytes"]), (4, 6500))
        a = d["roms"][0]
        self.assertEqual(a, {"path": str(self.a), "name": "Alpha.gba", "system": "GBA", "card": "primary", "size": 1000,
                             "extension": ".gba", "has_save": True, "has_state": False})

    def test_sort_name_and_size(self):
        _, d = self.run_cli("roms", "--sort", "size")
        self.assertEqual(self.names(d), ["Bravo.gba", "Charlie.gba", "Alpha.gba", "Đường Hầm Xuyên Việt.nes"])
        self.assertEqual(d["filters"]["sort"], "size")
        _, d = self.run_cli("roms", "--sort", "name")
        self.assertEqual(self.names(d), ["Alpha.gba", "Bravo.gba", "Charlie.gba", "Đường Hầm Xuyên Việt.nes"])

    def test_save_filter(self):
        _, d = self.run_cli("roms", "--save", "with")
        self.assertEqual(sorted(self.names(d)), ["Alpha.gba", "Charlie.gba"])
        _, d = self.run_cli("roms", "--save", "without")
        self.assertEqual(sorted(self.names(d)), ["Bravo.gba", "Đường Hầm Xuyên Việt.nes"])
        _, d = self.run_cli("roms", "--save", "any")
        self.assertEqual(d["count"], 4)

    def test_query_ignores_case_and_vietnamese_accents(self):
        for q in ("duong ham", "ĐƯỜNG", "xuyen viet", "đường hầm"):
            _, d = self.run_cli("roms", "--query", q)
            self.assertEqual(self.names(d), ["Đường Hầm Xuyên Việt.nes"], q)
        _, d = self.run_cli("roms", "--query", "ALPH")
        self.assertEqual(self.names(d), ["Alpha.gba"])
        _, d = self.run_cli("roms", "--query", "khongco")
        self.assertEqual((d["count"], d["roms"]), (0, []))

    def test_limit_and_combination(self):
        _, d = self.run_cli("roms", "--sort", "size", "--limit", "2")
        self.assertEqual(self.names(d), ["Bravo.gba", "Charlie.gba"])
        self.assertEqual((d["count"], d["matched"], d["total_bytes"]), (2, 4, 5000))
        _, d = self.run_cli("roms", "--save", "without", "--sort", "size", "--limit", "1", "--system", "GBA")
        self.assertEqual(self.names(d), ["Bravo.gba"])
        code, d = self.run_cli("roms", "--limit", "0")
        self.assertEqual(code, 2)


# ---------------------------------------------------------------- CLI khởi động nhẹ
class TestStartup(unittest.TestCase):
    HEAVY = ["engine.ui_screens", "engine.transfer", "engine.nodes", "engine.agent", "engine.health", "engine.remote",
             "engine.doctor", "engine.batchfix", "engine.dedupe", "engine.jawaka", "engine.telemetry", "engine.update"]

    def loaded_after(self, *cli_args):
        tmp = Path(tempfile.mkdtemp(prefix="loongmate_startup_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "sd1").mkdir()
        code = ("import runpy, sys, json\nsys.argv = ['cli.py'] + %r\n"
                "try:\n    runpy.run_path(%r, run_name='__main__')\nexcept SystemExit:\n    pass\n"
                "print('\\n@@' + json.dumps(sorted(m for m in sys.modules if m.startswith('engine'))))\n"
                % (["--primary", str(tmp / "sd1"), "--userdata", str(tmp / "ud"), "--quiet", "--json"] + list(cli_args),
                   str(ROOT / "engine" / "cli.py")))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", cwd=str(tmp))
        self.assertIn("@@", r.stdout, r.stderr)
        return set(json.loads(r.stdout.rsplit("@@", 1)[1])), r.stdout.rsplit("@@", 1)[0]

    def test_import_time_readiness_skips_heavy_modules(self):
        mods, out = self.loaded_after("readiness")
        self.assertIn('"offsite"', out, "readiness vẫn phải chạy thật và trả offsite")
        self.assertEqual(sorted(set(self.HEAVY) & mods), [])

    def test_import_time_light_commands(self):
        for cmd in (["snapshots"], ["agent", "status"]):
            mods, _ = self.loaded_after(*cmd)
            heavy = set(self.HEAVY) - {"engine.agent", "engine.telemetry"}      # agent status cần agent/telemetry
            self.assertEqual(sorted(heavy & mods), [], cmd)

    def test_ui_screens_still_reexports_offsite_status(self):
        from engine import status
        self.assertIs(ui.offsite_status, status.offsite_status)
        self.assertEqual(ui.OFFSITE_STALE_DAYS, status.OFFSITE_STALE_DAYS)

    def test_cli_module_is_impl_and_script_runs(self):
        import engine.cli_impl as impl
        self.assertIs(CLI, impl)
        r = subprocess.run([sys.executable, str(ROOT / "engine" / "cli.py"), "--help"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in sorted(CLI._COMMANDS):
            self.assertIn(cmd, r.stdout, "--help dựng đủ mọi lệnh")

    def test_command_set_matches_full_parser(self):
        full = CLI.build_parser()
        self.assertEqual(set(full._subparsers._group_actions[0].choices), CLI._COMMANDS)

    def test_restricted_parser_equals_full_parser(self):
        cases = [["readiness"], ["--json", "snapshots"], ["--userdata", "u", "--primary=p", "--quiet", "roms", "--system", "PSX",
                 "--sort", "size", "--limit", "3"], ["fix", "batch", "--only", "fix_cue", "create_m3u", "--apply"], ["fix", "undo", "--check"],
                 ["saves", "restore", "k", "--snapshot", "s", "--yes"], ["endpoint", "list"], ["rom", "delete", "a", "b", "--yes"],
                 ["--prim", "x", "agent", "status"], ["move", "--bulk", "--to", "secondary", "--target-free-mb", "5"]]
        for argv in cases:
            only = CLI._peek_command(argv)
            self.assertIsNotNone(only, argv)
            self.assertEqual(vars(CLI.build_parser(only).parse_args(argv)), vars(CLI.build_parser().parse_args(argv)), argv)
        self.assertIsNone(CLI._peek_command(["-h"]))
        self.assertIsNone(CLI._peek_command(["nope"]))
        self.assertIsNone(CLI._peek_command([]))


# ---------------------------------------------------------------- màn Xoá ROM: sắp xếp / lọc / game lớn nhất
class TestRomViewLogic(unittest.TestCase):
    def rows(self):
        mk = lambda n, size, card="primary": {"name": n + ".gba", "stem": n, "size": size, "card_id": card}
        return [mk("Alpha", 1000), mk("Bravo", 3000), mk("Charlie", 2000, "secondary"), mk("Delta", 500)]

    def test_sort_filter_top(self):
        rs, stems = self.rows(), {"alpha", "charlie"}
        names = lambda x: [r["stem"] for r in x]
        self.assertEqual(names(ui.rom_view_rows(rs, stems)), ["Alpha", "Bravo", "Charlie", "Delta"])
        self.assertEqual(names(ui.rom_view_rows(rs, stems, sort="size")), ["Bravo", "Charlie", "Alpha", "Delta"])
        self.assertEqual(names(ui.rom_view_rows(rs, stems, save="with")), ["Alpha", "Charlie"])
        self.assertEqual(names(ui.rom_view_rows(rs, stems, save="without", sort="size")), ["Bravo", "Delta"])
        self.assertEqual(names(ui.rom_view_rows(rs, stems, top=2, sort="name")), ["Bravo", "Charlie"])
        self.assertEqual(names(ui.rom_view_rows(rs, stems, save="with", top=1)), ["Charlie"], "lọc trước rồi mới lấy top")
        self.assertEqual(len(rs), 4, "không đổi đầu vào")


class TestRomViewScreen(Base):
    NAMES = {"Alpha": 1000, "Bravo": 3000, "Charlie": 2000, "Delta": 500}

    def setUp(self):
        super().setUp()
        self.files = {n: self.w(self.sd1 / f"Roms/GBA/{n}.gba", b"x" * sz) for n, sz in self.NAMES.items()}
        self.nes = self.w(self.sd2 / "Roms/NES/Huge.nes", b"n" * 9000)
        self.w(self.sd1 / "Saves/GBA/Alpha.srm", b"s")
        self.w(self.sd2 / "Saves/GBA/charlie.srm", b"s")
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        self.app = ui.App(probe, None, None, InventoryEngine(probe.primary_path, probe.secondary_path), None, self.ud, None)

    def enter(self, which="GBA"):
        self.app.open_rom_systems()
        self.app.wait_task()
        if which == ui.ROM_TOP:
            self.assertEqual(self.app.cur().items[0].label, "Game lớn nhất (mọi hệ)")
            self.app.on_action("A")
        else:
            self.app.open_rom_games(which)
        return self.app.cur()

    def games(self, scr):
        return [re.sub(r"\.(gba|nes|bin)$", "", it.label) for it in scr.items if hasattr(it, "rom_path")]

    def cursor_label(self, scr):
        return scr.items[scr.sel].label

    def go(self, scr, label):
        idx = [i for i, it in enumerate(scr.items) if re.sub(r"\.(gba|nes|bin)$", "", it.label).endswith(label)]
        self.assertTrue(idx, (label, [it.label for it in scr.items]))
        while scr.sel != idx[0]:
            self.app.on_action("DOWN" if scr.sel < idx[0] else "UP")

    def test_default_name_then_Y_sorts_by_size_and_header_says_so(self):
        scr = self.enter()
        self.assertEqual(self.games(scr), ["[ ] Alpha", "[ ] Bravo", "[ ] Charlie", "[ ] Delta"])
        self.assertIn("Sắp xếp: Tên", " ".join(scr.header))
        self.assertEqual(scr.items[0].label, "Lọc: tất cả", "mục Lọc ở đầu danh sách")
        self.go(scr, "Charlie")
        self.app.on_action("Y")
        self.assertEqual(self.games(scr), ["[ ] Bravo", "[ ] Charlie", "[ ] Alpha", "[ ] Delta"])
        self.assertIn("Sắp xếp: Dung lượng", " ".join(scr.header))
        self.assertEqual(self.cursor_label(scr), "[ ] Charlie.gba", "con trỏ ở lại đúng game")
        self.app.on_action("Y")
        self.assertEqual(self.games(scr), ["[ ] Alpha", "[ ] Bravo", "[ ] Charlie", "[ ] Delta"])

    def test_cursor_starts_on_first_game_not_on_filter(self):
        """Bấm A ngay khi vào màn phải mở game đầu tiên, không đổi bộ lọc."""
        for which in ("GBA", ui.ROM_TOP):
            scr = self.enter(which)
            self.assertTrue(hasattr(scr.items[scr.sel], "rom_path"), (which, self.cursor_label(scr)))
            self.app.on_action("B")

    def test_filter_cycles_with_A_and_marks_survive_sort_and_filter(self):
        scr = self.enter()
        self.go(scr, "Alpha")
        self.app.on_action("X")
        self.go(scr, "Bravo")
        self.app.on_action("X")
        marks = set(self.app.rom_marks["GBA"])
        self.assertEqual(len(marks), 2)
        self.assertEqual(scr.items[0].label.split()[0], "Xoá", "mục Xoá N game ở trên cùng")
        self.assertEqual(scr.items[1].label, "Lọc: tất cả", "mục Lọc nằm dưới mục Xoá")
        self.go(scr, "Lọc: tất cả")
        self.app.on_action("A")                                   # tất cả -> có save
        self.assertEqual(scr.items[scr.sel].label, "Lọc: có save", "con trỏ ở lại trên mục Lọc")
        self.assertEqual(self.games(scr), ["[x] Alpha", "[ ] Charlie"])
        self.app.on_action("Y")
        self.assertEqual(self.games(scr), ["[ ] Charlie", "[x] Alpha"])
        self.app.on_action("A")                                   # có save -> không save
        self.assertEqual(self.games(scr), ["[x] Bravo", "[ ] Delta"])
        self.assertIn("1 đang bị bộ lọc ẩn", " ".join(scr.header), "Alpha đang đánh dấu nhưng bị lọc ẩn")
        self.assertEqual(self.app.rom_marks["GBA"], marks, "đánh dấu giữ nguyên")
        self.app.on_action("A")                                   # không save -> tất cả
        self.assertEqual([g for g in self.games(scr) if g.startswith("[x]")], ["[x] Bravo", "[x] Alpha"])  # đang xếp theo dung lượng
        self.assertEqual(self.app.rom_marks["GBA"], marks)

    def test_hidden_marked_game_is_still_in_the_delete_plan(self):
        scr = self.enter()
        self.go(scr, "Alpha")
        self.app.on_action("X")
        self.go(scr, "Lọc: tất cả")
        self.app.on_action("A")
        self.app.on_action("A")                                   # -> không save: Alpha bị ẩn
        self.assertNotIn("[x] Alpha", self.games(scr))
        scr.sel = 0
        self.assertTrue(scr.items[0].label.startswith("Xoá 1 game"))
        self.app.on_action("A")
        self.app.wait_task()
        self.assertIn("Alpha.gba", "\n".join(self.app.dialog.lines), "game ẩn nhưng đã chọn vẫn nằm trong kế hoạch xoá")

    def test_top_list_is_50_largest_across_systems_and_deletes_in_place(self):
        for i in range(55):
            self.w(self.sd1 / f"Roms/PSX/G{i:02d}.bin", b"p" * (10000 + i))      # 55 file lớn hơn mọi game GBA
        self.app.inv = None
        scr = self.enter(ui.ROM_TOP)
        shown = self.games(scr)
        self.assertEqual(len(shown), 50)
        self.assertIn("Sắp xếp: Dung lượng", " ".join(scr.header))
        self.assertEqual(shown[0], "[ ] G54", "mặc định: lớn nhất trước")
        self.assertNotIn("[ ] G04", shown, "5 file nhỏ nhất của PSX không lọt top 50")
        self.assertNotIn("[ ] Huge", shown)                                        # NES 9000 B < mọi G*
        self.go(scr, "G54")
        self.app.on_action("X")
        self.go(scr, "G53")
        self.app.on_action("X")
        depth = len(self.app.stack)
        scr.sel = 0
        self.assertTrue(scr.items[0].label.startswith("Xoá 2 game"))
        self.app.on_action("A")
        self.app.wait_task()
        self.app.on_action("RIGHT")
        self.app.on_action("A")
        self.app.wait_task()
        self.assertFalse((self.sd1 / "Roms/PSX/G54.bin").exists() or (self.sd1 / "Roms/PSX/G53.bin").exists())
        self.assertIs(self.app.cur(), scr)
        self.assertEqual(len(self.app.stack), depth, "đứng yên tại chỗ sau khi xoá")
        shown = self.games(scr)
        self.assertEqual((len(shown), shown[0]), (50, "[ ] G52"), "danh sách lấy thêm game kế để đủ 50")
        self.assertEqual(self.app.rom_marks[ui.ROM_TOP], set())

    def test_system_screen_lists_top_item_first_with_count(self):
        self.app.open_rom_systems()
        self.app.wait_task()
        systems = self.app.cur()
        self.assertEqual([it.label for it in systems.items], ["Game lớn nhất (mọi hệ)", "GBA", "NES"])
        self.assertEqual(systems.items[0].tag, "5 game")
        self.assertIn("2 hệ máy", systems.header[0])


# ---------------------------------------------------------------- saves theo game
class VaultBase(Base):
    def setUp(self):
        super().setUp()
        self.vault = SaveVault(self.sd1, self.sd2, self.ud)
        self.z_srm = self.sd1 / "Saves/mGBA/Zelda (USA).srm"
        self.z_st = self.sd1 / "States/mGBA/Zelda (USA).state1"
        self.z_png = self.sd1 / "States/mGBA/Zelda (USA).state1.png"
        self.m_srm = self.sd1 / "Saves/mGBA/Mario (USA).srm"
        self.s_srm = self.sd2 / "Saves/Snes9x/Chrono Trigger (USA).srm"
        self.w(self.sd1 / "Roms/GBA/Zelda (USA).gba")
        self.clock = 0

    def snap(self, label="manual"):
        self.clock += 1
        # mtime cố định theo bước để "giống bản hiện tại"/"mới hơn snapshot" tất định
        r = self.vault.create_snapshot(label=label)
        self.assertTrue(r["success"], r)
        return r["snapshot_id"]

    def write(self, path, data, mtime):
        self.w(path, data)
        os.utime(path, (mtime, mtime))

    def sha(self, p):
        import hashlib
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def world(self):
        """3 snapshot: S1 (Zelda v1), S2 (Zelda v2 + state), S3 (Zelda v3). Mario/Chrono đổi nội dung ở S2."""
        T = 1_700_000_000
        self.write(self.z_srm, b"zelda-v1", T)
        self.write(self.m_srm, b"mario-1", T)
        self.write(self.s_srm, b"chrono-1", T)
        s1 = self.snap()
        self.write(self.z_srm, b"zelda-v2", T + 100)
        self.write(self.z_st, b"state-A", T + 100)
        self.write(self.z_png, b"png-A", T + 100)
        self.write(self.m_srm, b"mario-2", T + 100)
        s2 = self.snap()
        self.write(self.z_srm, b"zelda-v3", T + 200)
        s3 = self.snap()
        return s1, s2, s3


class TestGameOf(unittest.TestCase):
    def test_grouping(self):
        key = lambda r: game_of(r)[0]
        self.assertEqual({key("Saves/mGBA/Zelda (USA).srm"), key("States/mGBA/Zelda (USA).state1"),
                          key("States/mGBA/Zelda (USA).state1.png"), key("States/mGBA/zelda (usa).state"),
                          key("States/mGBA/ZELDA (USA).state.auto"), key("States/x/Zelda (USA).state12.png"),
                          key("Saves/PCSX/Zelda (USA).ldci")}, {"zelda (usa)"})
        self.assertEqual(game_of("States/mGBA/Zelda (USA).state1"), ("zelda (usa)", "Zelda (USA)", "mGBA"))
        self.assertEqual(game_of("States/Mupen/Game.st0")[0], "game")
        self.assertNotEqual(key("Saves/a/Zelda.srm"), key("Saves/a/Zelda II.srm"))
        self.assertEqual(key("Saves/MAME/mame/cfg/bagman.cfg"), "bagman")
        self.assertEqual(game_of("Saves/Flycast/vmu_save_A1.bin")[0], "vmu_save_a1")
        self.assertEqual(game_of("@x/PSP/SAVEDATA/ULUS10041/PARAM.SFO")[0], "ulus10041")


class TestSavesCli(VaultBase):
    def test_list_games_versions_and_files(self):
        s1, s2, s3 = self.world()
        code, d = self.run_cli("saves", "list")
        self.assertEqual(code, 0)
        self.assertEqual(list(d), ["games"])
        by = {g["key"]: g for g in d["games"]}
        self.assertEqual(sorted(by), ["chrono trigger (usa)", "mario (usa)", "zelda (usa)"])
        z = by["zelda (usa)"]
        self.assertEqual((z["name"], z["system"], z["card"], z["versions"]), ("Zelda (USA)", "GBA", "primary", 3))
        self.assertEqual(sorted(f["rel"] for f in z["files"]),
                         ["Saves/mGBA/Zelda (USA).srm", "States/mGBA/Zelda (USA).state1", "States/mGBA/Zelda (USA).state1.png"])
        zf = next(f for f in z["files"] if f["rel"].endswith(".srm"))
        self.assertEqual((zf["path"], zf["size"]), (str(self.z_srm.resolve()), len(b"zelda-v3")))
        self.assertEqual(set(zf), {"path", "rel", "size", "mtime"})
        self.assertEqual((by["chrono trigger (usa)"]["card"], by["chrono trigger (usa)"]["system"]), ("secondary", None))
        self.assertEqual(by["mario (usa)"]["versions"], 3)
        self.assertEqual(set(z), {"key", "name", "system", "core", "card", "files", "versions"})

    def test_versions_counts_only_snapshots_with_the_game(self):
        self.world()
        self.write(self.sd1 / "Saves/mGBA/Kirby.srm", b"k", 1_700_000_300)
        self.snap()
        _, d = self.run_cli("saves", "list")
        by = {g["key"]: g for g in d["games"]}
        self.assertEqual(by["kirby"]["versions"], 1)
        self.assertEqual(by["zelda (usa)"]["versions"], 4)

    def test_game_only_in_snapshot_is_listed_and_history_marks_null(self):
        self.world()
        self.m_srm.unlink()
        code, d = self.run_cli("saves", "list")
        mario = next(g for g in d["games"] if g["key"] == "mario (usa)")
        self.assertEqual((mario["files"], mario["versions"], mario["name"]), ([], 3, "Mario (USA)"))
        _, h = self.run_cli("saves", "history", "mario (usa)")
        self.assertTrue(all(f["same_as_current"] is None for v in h["versions"] for f in v["files"]))

    def test_history_newest_first_and_same_as_current(self):
        s1, s2, s3 = self.world()
        code, h = self.run_cli("saves", "history", "Zelda (USA)")        # khoá không phân biệt hoa/thường
        self.assertEqual(code, 0)
        self.assertEqual(list(h), ["key", "name", "versions"])
        self.assertEqual([v["snapshot_id"] for v in h["versions"]], [s3, s2, s1])
        self.assertEqual(set(h["versions"][0]), {"snapshot_id", "label", "created_at", "files"})
        v3, v2, v1 = h["versions"]
        self.assertEqual(h["versions"][0]["label"], "manual")
        same = lambda v: {f["rel"].split("/")[-1]: f["same_as_current"] for f in v["files"]}
        self.assertEqual(same(v3), {"Zelda (USA).srm": True, "Zelda (USA).state1": True, "Zelda (USA).state1.png": True})
        self.assertEqual(same(v2)["Zelda (USA).srm"], False)
        self.assertEqual(same(v2)["Zelda (USA).state1"], True)
        self.assertEqual([f["rel"] for f in v1["files"]], ["Saves/mGBA/Zelda (USA).srm"], "S1 chưa có state")
        f = v1["files"][0]
        self.assertEqual((f["size"], f["sha256"], f["same_as_current"]), (8, self.sha_of(b"zelda-v1"), False))
        code, e = self.run_cli("saves", "history", "khong co")
        self.assertEqual((code, e["error"]), (1, "GAME_NOT_FOUND"))

    @staticmethod
    def sha_of(b):
        import hashlib
        return hashlib.sha256(b).hexdigest()

    def snapshot_ids(self):
        return [s["id"] for s in self.vault.list_snapshots()]

    def test_restore_without_yes_writes_nothing(self):
        s1, s2, s3 = self.world()
        before_ids, before = self.snapshot_ids(), self.z_srm.read_bytes()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s1)
        self.assertEqual(code, 1)
        self.assertEqual((d["success"], d["preview"], d["requires_confirmation"], d["will_write"]), (False, True, True, 1))
        self.assertEqual({f["rel"].split("/")[-1]: f["action"] for f in d["files"]}, {"Zelda (USA).srm": "overwrite"})
        self.assertEqual(self.z_srm.read_bytes(), before, "file không đổi")
        self.assertEqual(self.snapshot_ids(), before_ids, "không tạo pre-restore snapshot khi chỉ xem trước")
        self.assertFalse((self.ud / "SaveVault" / "restore_journal.json").exists())

    def test_restore_preview_lists_create_overwrite_unchanged(self):
        s1, s2, s3 = self.world()
        self.z_st.unlink()                                               # S2 có state; hiện tại không có -> create
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s2)
        acts = {f["rel"].split("/")[-1]: f["action"] for f in d["files"]}
        self.assertEqual(acts, {"Zelda (USA).srm": "overwrite", "Zelda (USA).state1": "create", "Zelda (USA).state1.png": "unchanged"})
        self.assertEqual(d["will_write"], 2)

    def test_restore_only_writes_that_game(self):
        s1, s2, s3 = self.world()
        others = {p: (p.read_bytes(), os.stat(p).st_mtime_ns) for p in (self.m_srm, self.s_srm)}
        state_before = self.z_st.read_bytes()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s1, "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-v1")
        self.assertEqual(self.sha(self.z_srm), self.sha_of(b"zelda-v1"))
        self.assertEqual(self.z_st.read_bytes(), state_before, "S1 không có state: state hiện tại phải còn nguyên")
        for p, (data, mt) in others.items():
            self.assertEqual((p.read_bytes(), os.stat(p).st_mtime_ns), (data, mt), f"{p.name} (game khác) không được đụng")
        self.assertEqual(d["restored"], ["Saves/mGBA/Zelda (USA).srm"])
        self.assertTrue(d["success"] and d["verified"] and d["failed"] == [])
        self.assertEqual(d["snapshot_id"], s1)

    def test_restore_creates_pre_restore_snapshot_that_can_undo(self):
        s1, s2, s3 = self.world()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s1, "--yes")
        pre = d["pre_restore_id"]
        meta = next(s for s in self.vault.list_snapshots() if s["id"] == pre)
        self.assertEqual(meta["label"], "pre_restore")
        _, h = self.run_cli("saves", "history", "zelda (usa)")
        self.assertIn(pre, [v["snapshot_id"] for v in h["versions"]], "đường lùi hiện trong lịch sử của chính game")
        code, d2 = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", pre, "--yes")
        self.assertEqual(code, 0, d2)
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-v3", "quay lại đúng bản đang chơi trước khi khôi phục")

    def test_restore_creates_missing_files(self):
        s1, s2, s3 = self.world()
        self.z_st.unlink()
        self.z_png.unlink()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s2, "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual((self.z_st.read_bytes(), self.z_png.read_bytes(), self.z_srm.read_bytes()),
                         (b"state-A", b"png-A", b"zelda-v2"))

    def test_restore_nothing_to_do_creates_no_snapshot(self):
        s1, s2, s3 = self.world()
        before = self.snapshot_ids()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s3, "--yes")
        self.assertEqual((code, d["nothing_to_do"], d["restored"]), (0, True, []))
        self.assertEqual(self.snapshot_ids(), before)

    def test_restore_errors(self):
        s1, s2, s3 = self.world()
        before = self.snapshot_ids()
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", "snap_khong_co", "--yes")
        self.assertEqual((code, d["error"]), (1, "SNAPSHOT_NOT_FOUND"))
        self.write(self.sd1 / "Saves/mGBA/Kirby.srm", b"k", 1_700_000_400)
        s4 = self.snap()
        code, d = self.run_cli("saves", "restore", "kirby", "--snapshot", s1, "--yes")
        self.assertEqual((code, d["error"]), (1, "GAME_NOT_IN_SNAPSHOT"))
        code, d = self.run_cli("saves", "restore", "khong co", "--snapshot", s1, "--yes")
        self.assertEqual((code, d["error"]), (1, "GAME_NOT_FOUND"))
        for bad in ("../x", "..", "a/b"):
            code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", bad, "--yes")
            self.assertEqual(code, 1, bad)
        self.assertEqual(self.snapshot_ids(), [s4] + before, "lệnh lỗi không tạo thêm snapshot nào (chỉ s4 do test tự tạo)")
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-v3")

    def test_restore_oldest_snapshot_when_retention_full(self):
        """5 snapshot thủ công (đúng hạn mức): tạo pre-restore sẽ prune cái cũ nhất — chính là snapshot đang khôi phục."""
        T = 1_700_000_000
        ids = []
        for i in range(5):
            self.write(self.z_srm, f"zelda-{i}".encode(), T + i * 10)
            ids.append(self.snap())
        self.assertEqual(len(self.snapshot_ids()), 5)
        code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", ids[0], "--yes")
        self.assertEqual(code, 0, d)
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-0", "phải ghi thật bản cũ nhất, không được lặng lẽ ghi 0 file")
        self.assertTrue(d["verified"])
        self.assertIn(ids[0], self.snapshot_ids(), "snapshot nguồn còn đó sau khi khôi phục")

    def test_restore_reports_unverified_when_destination_corrupted(self):
        s1, s2, s3 = self.world()
        real = sv_mod._write_file_atomic

        def corrupt(src, dst, tmp_suffix=".partial"):
            real(src, dst, tmp_suffix)
            if str(dst).endswith("Zelda (USA).srm") and tmp_suffix == ".restore.tmp":
                Path(dst).write_bytes(b"garbage")
        with mock.patch.object(sv_mod, "_write_file_atomic", corrupt):
            code, d = self.run_cli("saves", "restore", "zelda (usa)", "--snapshot", s1, "--yes")
        self.assertEqual(code, 1)
        self.assertEqual((d["success"], d["verified"], d["failed"]), (False, False, ["Saves/mGBA/Zelda (USA).srm"]))
        self.assertIn(d["pre_restore_id"], d["message"])


# ---------------------------------------------------------------- màn SaveVault: khôi phục một game
class TestSavesUi(VaultBase):
    def setUp(self):
        super().setUp()
        self.s1, self.s2, self.s3 = self.world()
        probe = PlatformProbe(str(self.sd1), str(self.sd2), str(self.ud))
        self.app = ui.App(probe, self.vault, None, None, None, self.ud, None)
        self.app.refresh_home()

    def press(self, *acts):
        for a in acts:
            self.app.on_action(a)
            self.app.wait_task()

    def goto(self, scr, label):
        idx = [i for i, it in enumerate(scr.items) if it.label.startswith(label)]
        self.assertTrue(idx, (label, [it.label for it in scr.items]))
        scr.sel = idx[0]

    def open_versions(self, game="Zelda (USA)"):
        self.app.open_savevault()
        sv = self.app.cur()
        self.goto(sv, "Khôi phục save một game")
        self.press("A")
        games = self.app.cur()
        self.assertEqual(games.title, "KHÔI PHỤC SAVE — chọn game")
        self.goto(games, game)
        self.press("A")
        return games, self.app.cur()

    def test_game_list_shows_version_counts(self):
        games, _ = self.open_versions()
        tags = {it.label: it.tag for it in games.items}
        self.assertEqual(tags, {"Chrono Trigger (USA)": "3 bản", "Mario (USA)": "3 bản", "Zelda (USA)": "3 bản"})

    def test_version_list_newest_first_with_same_as_current_tag(self):
        _, ver = self.open_versions()
        self.assertEqual(len(ver.items), 3)
        self.assertTrue(ver.items[0].tag == "giống hiện tại", "S3 giống hệt bản đang chơi")
        self.assertNotEqual(ver.items[1].tag, "giống hiện tại")
        self.assertIn(self.s3, ver.items[0].explain[0])
        self.assertIn(self.s1, ver.items[2].explain[0])
        self.assertTrue(any("GIỐNG bản hiện tại" in l for l in ver.items[0].explain))
        self.assertTrue(any("khác bản hiện tại" in l for l in ver.items[2].explain))

    def test_cancel_by_default_writes_nothing(self):
        _, ver = self.open_versions()
        ver.sel = 2                                           # S1
        before = self.vault.list_snapshots()
        self.press("A")                                       # xem trước + hộp xác nhận
        d = self.app.dialog
        self.assertIsNotNone(d.on_confirm)
        self.assertEqual(d.choice, 0, "mặc định Huỷ")
        self.assertIn("[GHI ĐÈ] Saves/mGBA/Zelda (USA).srm", "\n".join(d.lines))
        self.press("A")                                       # bấm nút đang chọn = Huỷ
        self.assertIsNone(self.app.dialog)
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-v3")
        self.assertEqual(self.vault.list_snapshots(), before)

    def test_confirm_restores_only_that_game_and_reports_way_back(self):
        _, ver = self.open_versions()
        ver.sel = 2
        mario = self.m_srm.read_bytes()
        depth = 2
        self.press("A", "RIGHT", "A")                          # xem trước, chọn Khôi phục, xác nhận
        self.assertEqual(self.z_srm.read_bytes(), b"zelda-v1")
        self.assertEqual(self.m_srm.read_bytes(), mario)
        self.assertEqual(len(self.app.stack), depth, "về màn SaveVault")
        text = "\n".join(self.app.dialog.lines)
        self.assertEqual(self.app.dialog.title, "Kết quả khôi phục save")
        pre = next(s["id"] for s in self.vault.list_snapshots() if s["label"] == "pre_restore")
        self.assertIn(pre, text, "nêu snapshot pre-restore để quay lại")
        self.assertIn("khớp snapshot", text)
        self.assertIsNone(self.app.game_idx, "danh sách cũ bị bỏ")

    def test_identical_version_says_nothing_to_do(self):
        _, ver = self.open_versions()
        ver.sel = 0                                            # S3 == hiện tại
        self.press("A")
        self.assertIsNone(self.app.dialog.on_confirm)
        self.assertIn("giống hệt", "\n".join(self.app.dialog.lines))


if __name__ == "__main__":
    unittest.main()
