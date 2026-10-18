"""Mã lỗi ổn định (docs/MA-LOI.md): mọi loại issue Library Doctor tạo ra đều có mã LM-..., mã không trùng nhau, tiền tố
dài được ưu tiên (MISSING_BIOS_ANYOF_ không thành MISSING_BIOS_), và tài liệu liệt kê đủ mọi mã."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import doctor as D  # noqa: E402
from tests.test_doctor_leaf import Base  # noqa: E402


class IssueCodes(unittest.TestCase):
    def test_every_issue_id_in_doctor_source_has_a_code(self):
        src = (ROOT / "engine" / "doctor.py").read_text(encoding="utf-8")
        ids = re.findall(r'"id":\s*f?"([A-Z0-9_]+)', src)
        self.assertGreater(len(ids), 20)
        missing = sorted({i for i in ids if D.issue_code(i + "X") == "LM-000" and D.issue_code(i) == "LM-000"})
        self.assertEqual(missing, [], "loại issue chưa có mã trong ISSUE_CODES")

    def test_codes_are_unique_and_well_formed(self):
        codes = [c for _, c in D.ISSUE_CODES]
        self.assertEqual(len(codes), len(set(codes)))
        for c in codes:
            self.assertRegex(c, r"^LM-[A-Z0-9]+-\d{3}$")

    def test_longest_prefix_wins(self):
        self.assertEqual(D.issue_code("MISSING_BIOS_ANYOF_PSX_scph1001"), "LM-BIOS-001")
        self.assertEqual(D.issue_code("MISSING_BIOS_OPTIONAL_PSX"), "LM-BIOS-002")
        self.assertEqual(D.issue_code("MISSING_BIOS_PSX"), "LM-BIOS-004")
        self.assertEqual(D.issue_code("SOMETHING_NEW"), "LM-000")

    def test_docs_list_every_code(self):
        doc = (ROOT / "docs" / "MA-LOI.md").read_text(encoding="utf-8")
        for _, c in D.ISSUE_CODES:
            self.assertIn(f"`{c}`", doc)


class AuditAttachesCodes(Base):
    def test_each_issue_from_a_real_audit_carries_its_code(self):
        self.leaf()
        self.w(self.sd1 / "Roms/CPS1/1941.zip", b"ROM")
        self.w(self.sd1 / "Roms/GBA/game.gba", b"ROM")
        rep = self.audit()
        self.assertTrue(rep["issues"])
        for i in rep["issues"]:
            self.assertEqual(i["code"], D.issue_code(i["id"]))
            self.assertNotEqual(i["code"], "LM-000", i["id"])


if __name__ == "__main__":
    unittest.main()
