import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class BrowserEnterpriseContractTests(unittest.TestCase):
    def test_contact_briefing_separates_legal_name_from_phone_user(self):
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        for marker in ("电话｜联系人/称呼｜职务｜号码归属依据｜确认状态", "法人姓名不等于号码使用人", "未披露/未确认", "请问怎么称呼您"):
            self.assertIn(marker, reference)

    def test_browser_only_request_routes_before_paid_preflight(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/browser-enterprise.md", skill)
        self.assertLess(skill.index("浏览器只读模式"), skill.index("## 就绪门槛"))
        self.assertIn("企业查询默认使用网页", skill)
        self.assertIn("重新明确授权付费API", skill)
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        for marker in ("preflight.py", "不调用", "riskbird company", "工商注册号", "单源", "验证码", "--registration-number"):
            self.assertIn(marker, reference)

    def test_contact_delivery_template_requires_evidence_status_and_opening(self):
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        template = reference.split("### 固定交付模板", 1)[1].split("## 成本、权限与停止", 1)[0]
        rows = [line.split("|")[1].strip() for line in template.splitlines() if line.startswith("|")]
        self.assertEqual(rows[2:], ["公司/店铺", "电话", "联系人/建议称呼", "职务", "号码归属依据", "确认状态", "建议开场"])

    def test_contact_decisions_do_not_merge_people_or_hide_conflicts(self):
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        for scenario in ("每个号码独立核验", "来源明确标注（未拨通）", "来源冲突，暂停认定", "不把一个姓名批量分配给全部电话", "不按姓名猜测性别"):
            self.assertIn(scenario, reference)

    def test_normalize_target_strips_riskbird_tracking_parameters(self):
        from browser_enterprise import normalize_target
        target = normalize_target({
            "shop_name": "示例市星辰日用品有限公司",
            "company": "示例市星辰日用品有限公司",
            "credit_code": "91320382MA000000X1",
            "source_url": "https://www.riskbird.com/ent/%E7%A4%BA%E4%BE%8B.html?entid=abc123&fuzzyId=456&position=1",
        })
        self.assertEqual(target["source_url"],
                         "https://www.riskbird.com/ent/%E7%A4%BA%E4%BE%8B.html?entid=abc123")
        with self.assertRaises(ValueError):
            normalize_target({
                "shop_name": "示例市星辰日用品有限公司",
                "company": "示例市星辰日用品有限公司",
                "credit_code": "91320382MA000000X1",
                "source_url": "https://example.com/ent/x.html?entid=abc123",
            })


if __name__ == "__main__":
    unittest.main()
