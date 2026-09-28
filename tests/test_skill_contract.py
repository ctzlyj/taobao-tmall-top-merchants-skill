import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_entrypoint_is_progressive_and_routes_to_executable_pipeline(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLess(len(skill), 6000)
        self.assertIn("enterprise_pipeline.py", skill)
        self.assertIn("references/enterprise-workflow.md", skill)
        for requirement in ("审核筛选模式", "用户指定名单模式", "类目发现模式", "双", "platform_qualifications.json", "不代表全店"):
            self.assertIn(requirement, skill)

    def test_recovery_contract_binds_identity_category_and_time(self):
        contract = (ROOT / "references/review-data-contract.md").read_text(encoding="utf-8")
        for field in ("rule_fingerprint", "captured_at", "schema_version", "category", "v1.bak", "24"):
            self.assertIn(field, contract)

    def test_enterprise_reference_explains_mvp_and_failure_recovery(self):
        reference = ROOT / "references/enterprise-workflow.md"
        self.assertTrue(reference.is_file())
        text = reference.read_text(encoding="utf-8")
        for requirement in ("--limit 1", "--limit 10", "--execute", "enterprise_calls.json", "enterprise_checkpoint.json", "不支持", "回滚"):
            self.assertIn(requirement, text)


if __name__ == "__main__":
    unittest.main()
