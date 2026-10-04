import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_phone_hunt import email_phone_hints, reconcile, reconcile_document


class PublicPhoneHuntContractTests(unittest.TestCase):
    def test_skill_and_references_route_to_public_phone_hunt(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/public-phone-hunt.md", skill)
        reference = (ROOT / "references" / "public-phone-hunt.md").read_text(encoding="utf-8")
        for marker in ("会员墙", "受限不等于未披露", "public_phone_hunt.py",
                       "不绕过", "信用代码", "gsxt"):
            self.assertIn(marker, reference)
        belt = (ROOT / "references" / "local-belt-visit.md").read_text(encoding="utf-8")
        self.assertIn("public-phone-hunt.md", belt)
        aiqicha = (ROOT / "references" / "aiqicha-web.md").read_text(encoding="utf-8")
        self.assertIn("public-phone-hunt.md", aiqicha)

    def test_reference_keeps_restricted_platform_gate(self):
        reference = (ROOT / "references" / "public-phone-hunt.md").read_text(encoding="utf-8")
        for marker in ("验证码", "单源", "不新增可拨电话", "2026-10-04", "徐州瑞利木业"):
            self.assertIn(marker, reference)


class FragmentReconcileTests(unittest.TestCase):
    def setUp(self):
        self.company = "徐州瑞利木业有限公司"
        self.credit_code = "9132032159558848XL"

    def fragment(self, value, source, label="", url="https://example.com/"):
        return {"value": value, "source": source, "source_url": url, "label": label}

    def test_three_sources_cross_validate_the_annual_report_phone(self):
        result = reconcile(self.company, [
            self.fragment("1586219****", "aiqicha_web", "2025年报"),
            self.fragment("****2198552", "yellowurl"),
            self.fragment("15862198552", "baidu_baike"),
        ], self.credit_code)
        self.assertEqual(result["phones"][0]["number"], "15862198552")
        self.assertEqual(result["phones"][0]["status"], "cross_validated")
        self.assertEqual(len(result["phones"][0]["sources"]), 3)

    def test_complementary_masks_determine_number_without_any_full_source(self):
        result = reconcile(self.company, [
            self.fragment("1586219****", "aiqicha_web", "2025年报"),
            self.fragment("****2198552", "yellowurl"),
        ], self.credit_code)
        self.assertEqual(result["phones"][0]["number"], "15862198552")
        self.assertEqual(result["phones"][0]["status"], "cross_validated")

    def test_single_full_number_stays_single_source(self):
        result = reconcile(self.company, [self.fragment("15862198552", "baidu_baike")])
        self.assertEqual(result["phones"][0]["status"], "single_source")

    def test_credit_code_substring_is_dropped_as_false_positive(self):
        result = reconcile(self.company, [
            self.fragment("13203215955", "page_html"),
            self.fragment("1586219****", "aiqicha_web", "2025年报"),
            self.fragment("****2198552", "yellowurl"),
        ], self.credit_code)
        numbers = [phone["number"] for phone in result["phones"]]
        self.assertNotIn("13203215955", numbers)
        self.assertEqual(result["dropped"][0]["reason"], "credit_code_substring")

    def test_unmatched_masks_stay_masked_instead_of_being_guessed(self):
        result = reconcile(self.company, [
            self.fragment("1586219****", "aiqicha_web", "2025年报"),
            self.fragment("1381536****", "aiqicha_web", "2022年报"),
        ], self.credit_code)
        self.assertEqual([phone["number"] for phone in result["phones"]], [])
        self.assertEqual([entry["masked"] for entry in result["unresolved"]],
                         [["1586219****"], ["1381536****"]])
        self.assertEqual([entry["labels"] for entry in result["unresolved"]],
                         [["2025年报"], ["2022年报"]])

    def test_fullwidth_and_x_masks_normalize(self):
        result = reconcile(self.company, [
            self.fragment("1586219＊＊＊＊", "aiqicha_web", "2025年报"),
            self.fragment("****2198552", "yellowurl"),
        ], self.credit_code)
        self.assertEqual(result["phones"][0]["number"], "15862198552")

    def test_email_local_part_mobile_becomes_hint_only(self):
        hints = email_phone_hints(["15252112888@qq.com", "office@example.com"])
        self.assertEqual(hints, [{"email": "15252112888@qq.com", "value": "15252112888",
                                  "status": "email_prefix_hint"}])

    def test_cli_reconciles_document(self):
        document = {"companies": [{
            "company": self.company, "credit_code": self.credit_code,
            "fragments": [
                self.fragment("1586219****", "aiqicha_web", "2025年报"),
                self.fragment("****2198552", "yellowurl"),
                self.fragment("15862198552", "baidu_baike")],
            "emails": ["15252112888@qq.com"]}]}
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "hunt.json"
            target = Path(folder) / "reconciled.json"
            source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "scripts" / "public_phone_hunt.py"),
                            "--input", str(source), "--output", str(target)],
                           check=True, capture_output=True)
            result = json.loads(target.read_text(encoding="utf-8"))
            record = result["companies"][0]
            self.assertEqual(record["phones"][0]["number"], "15862198552")
            self.assertEqual(record["email_hints"][0]["value"], "15252112888")


if __name__ == "__main__":
    unittest.main()
