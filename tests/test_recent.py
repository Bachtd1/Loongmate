"""`recent`: game cài gần đây, đọc <thẻ>/.loongmate-import/installed.jsonl đúng định dạng importer ghi (importer.py, lúc commit)."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import cli_impl


def _entry(at, name, system, path, size, kind="rom", job="j1"):
    return {"at": at, "job": job, "id": "u1", "name": name, "kind": kind, "system": system, "source": "Import/" + name,
            "files": [{"path": path, "size": size, "sha256": "0" * 64}], "replaced": []}


class TestRecent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sd1, self.sd2 = self.tmp / "sd1", self.tmp / "sd2"
        self.probe = mock.Mock(primary_path=str(self.sd1), secondary_path=str(self.sd2))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, card_root, *lines):
        d = card_root / ".loongmate-import"
        d.mkdir(parents=True, exist_ok=True)
        (d / "installed.jsonl").write_text("".join(l + "\n" for l in lines), encoding="utf-8")

    def _run(self, card=None, limit=50):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_impl._cmd_recent(mock.Mock(card=card, limit=limit, json=True), self.probe)
        self.assertEqual(code, 0)
        return json.loads(buf.getvalue())["recent"]

    def test_empty_when_no_ledger(self):
        self.assertEqual(self._run(), [])

    def test_newest_first_across_cards_skips_bios_and_bad_lines(self):
        rom = self.sd1 / "Roms" / "GBA" / "Hero.gba"
        rom.parent.mkdir(parents=True)
        rom.write_bytes(b"x" * 10)
        self._write(self.sd1,
                    json.dumps(_entry("2026-10-10T10:00:00Z", "Hero", "GBA", "Roms/GBA/Hero.gba", 10)),
                    "{không phải json",
                    json.dumps(_entry("2026-10-12T10:00:00Z", "gba_bios", "GBA", "BIOS/gba_bios.bin", 16384, kind="bios")))
        self._write(self.sd2, json.dumps(_entry("2026-10-15T15:00:00Z", "Gone", "SFC", "Roms/SFC/Gone.sfc", 20)))
        r = self._run()
        self.assertEqual([x["name"] for x in r], ["Gone", "Hero"])
        self.assertEqual((r[0]["card"], r[0]["exists"], r[0]["size"]), ("secondary", False, 20))
        self.assertEqual((r[1]["card"], r[1]["exists"], r[1]["system"]), ("primary", True, "GBA"))
        self.assertEqual(r[1]["target_file"], str(rom))
        self.assertEqual([x["name"] for x in self._run(card="primary")], ["Hero"])
        self.assertEqual(len(self._run(limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
