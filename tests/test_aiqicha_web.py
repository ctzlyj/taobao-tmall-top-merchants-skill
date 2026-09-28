import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from browser_contacts import contact_rows
from browser_enterprise import normalize_target


class AiqichaWebContractTests(unittest.TestCase):
    def test_skill_routes_to_aiqicha_web_reference(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("爱企查", skill)
        self.assertIn("references/aiqicha-web.md", skill)
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        self.assertIn("aiqicha-web.md", reference)
        self.assertIn("风鸟、企查查、爱企查", reference)

    def test_source_is_web_only_and_not_a_paid_provider(self):
        reference = (ROOT / "references/aiqicha-web.md").read_text(encoding="utf-8")
        for marker in ("https://m.aiqicha.com/", "aiqicha_web", "不调用付费API", "不安装或启用爱企查MCP", "browser_targets.json"):
            self.assertIn(marker, reference)

    def test_debug_gate_does_not_become_missing_contact_or_tool_bypass(self):
        reference = (ROOT / "references/aiqicha-web.md").read_text(encoding="utf-8")
        for marker in ("请关闭浏览器的调试窗口再访问页面", "access_blocked", "not_queried", "not_disclosed", "不更换工具、账号或端点绕过", "人工"):
            self.assertIn(marker, reference)

    def test_evidence_and_live_acceptance_boundaries_are_explicit(self):
        reference = (ROOT / "references/aiqicha-web.md").read_text(encoding="utf-8")
        for marker in ("信用代码", "工商注册号", "联系人", "法人", "未拨通", "实际详情URL", "未完成", "不能宣称"):
            self.assertIn(marker, reference)


class AiqichaContactEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.record = {"company": "示例公司", "phones": ["010-12345678", "010-12345679"]}
        self.evidence = {
            "phone": "010-12345678", "person": "张经理", "role": "商务联系人", "company": "示例公司",
            "source_url": "https://m.aiqicha.com/fixture-company",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "association": "explicit", "excerpt": "商务联系人：张经理；电话：010-12345678",
        }

    def test_visible_contact_evidence_preserves_aiqicha_source_per_phone(self):
        rows = contact_rows(self.record, [self.evidence])
        self.assertEqual(rows[0]["person"], "张经理")
        self.assertEqual(rows[0]["confirmation"], "来源明确标注（未拨通）")
        self.assertEqual(rows[0]["evidence"][0]["source_url"], self.evidence["source_url"])
        self.assertEqual(rows[1]["person"], "未披露/未确认")

    def test_source_conflict_does_not_replace_existing_contact(self):
        other = {**self.evidence, "source_url": "https://www.qcc.com/fixture-company",
                 "person": "示例乙", "excerpt": "商务联系人：示例乙；电话：010-12345678"}
        row = contact_rows(self.record, [self.evidence, other])[0]
        self.assertEqual(row["confirmation"], "来源冲突，暂停认定")
        self.assertEqual(len(row["evidence"]), 2)

    def test_legal_name_is_not_number_owner(self):
        evidence = {**self.evidence, "association": "legal_representative"}
        row = contact_rows(self.record, [evidence])[0]
        self.assertEqual(row["person"], "未披露/未确认")

    def test_private_query_parameters_are_rejected(self):
        evidence = {**self.evidence, "source_url": self.evidence["source_url"] + "?token=fixture"}
        with self.assertRaisesRegex(ValueError, "INVALID_SOURCE_URL"):
            contact_rows(self.record, [evidence])

    def test_aiqicha_url_is_not_sent_to_riskbird_adapter(self):
        with self.assertRaisesRegex(ValueError, "INVALID_SOURCE_URL"):
            normalize_target({"company": "示例公司", "credit_code": "913100000000000001",
                              "source_url": self.evidence["source_url"]})


if __name__ == "__main__":
    unittest.main()
