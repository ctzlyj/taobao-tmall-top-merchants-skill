import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalBeltDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import sys
        self.modules_backup = dict(sys.modules)
        sys.path.insert(0, str(ROOT / "scripts"))
        import local_belt_discovery
        self.module = local_belt_discovery

    def tearDown(self):
        import sys
        sys.path.remove(str(ROOT / "scripts"))
        for name, module in self.modules_backup.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module

    def test_build_queries_combines_region_keywords_suffixes_and_dedupes(self):
        queries = self.module.build_queries("示例市", ["沐浴", "木梳"], ["制造"])
        self.assertEqual(queries, ["示例市 沐浴", "示例市 沐浴 制造", "示例市 木梳", "示例市 木梳 制造"])
        self.assertEqual(self.module.build_queries("示例市", ["沐浴", "沐浴"]), ["示例市 沐浴"])
        with self.assertRaises(ValueError):
            self.module.build_queries("", ["沐浴"])
        with self.assertRaises(ValueError):
            self.module.build_queries("示例市", [])

    def test_parse_card_extracts_synthetic_company_fields(self):
        card = ("示例市星辰日用品有限公司 存续\n"
                "法定代表人：张示例 注册资本：100万元 成立日期：2020-01-01 "
                "统一社会信用代码：91320382MA000000X1\n"
                "电话：13800000000 邮箱：example@example.com\n"
                "地址：示例市示例街道示例路1号复制\n"
                "经营范围： 一般项目:家居用品制造;日用品销售")
        row = self.module.parse_card(card)
        self.assertEqual(row["name"], "示例市星辰日用品有限公司")
        self.assertEqual(row["status"], "存续")
        self.assertEqual(row["legal"], "张示例")
        self.assertEqual(row["credit"], "91320382MA000000X1")
        self.assertEqual(row["phone"], "13800000000")
        self.assertEqual(row["email"], "example@example.com")
        self.assertIn("家居用品制造", row["scope"])

    def test_merge_companies_dedupes_and_backfills_without_losing_queries(self):
        first = {"query": "示例市 沐浴", "cards": [
            "示例市星辰日用品有限公司 存续 统一社会信用代码：91320382MA000000X1 "
            "法定代表人：张示例 电话：13800000000 地址：示例市示例街道示例路1号复制 经营范围： 日用品销售"]}
        second = {"query": "示例市 家居", "cards": [
            "示例市星辰日用品有限公司 存续 统一社会信用代码：91320382MA000000X1 "
            "法定代表人：张示例 邮箱：example@example.com 经营范围： 家居用品制造"]}
        merged = self.module.merge_companies([first, second], "示例市")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["queries"], ["示例市 沐浴", "示例市 家居"])
        self.assertEqual(merged[0]["email"], "example@example.com")
        self.assertIn("13800000000", merged[0]["raw"])
        self.assertGreater(merged[0]["score"], 0)

    def test_relevance_penalizes_cancelled_status_and_rewards_region(self):
        active = {"raw": "示例市甲制品厂 存续 家居用品制造", "status": "存续", "scope": "", "region": "示例市"}
        cancelled = {"raw": "示例市乙制品厂 注销 家居用品制造", "status": "注销", "scope": "", "region": "示例市"}
        outside = {"raw": "外地丙制品厂 存续 家居用品制造", "status": "存续", "scope": "", "region": "示例市"}
        self.assertGreater(self.module.relevance(active), self.module.relevance(cancelled))
        self.assertGreater(self.module.relevance(active), self.module.relevance(outside))

    def test_pick_riskbird_link_prefers_exact_name_and_rejects_far_matches(self):
        search_row = {"cards": [{
            "links": [
                {"name": "示例市星辰日用品有限公司", "url": "https://www.riskbird.com/ent/a.html?entid=one"},
                {"name": "示例市星辰日用品集团服务有限公司",
                 "url": "https://www.riskbird.com/ent/b.html?entid=two"},
            ]
        }]}
        self.assertEqual(self.module.pick_riskbird_link(search_row, "示例市星辰日用品有限公司"),
                         "https://www.riskbird.com/ent/a.html?entid=one")
        self.assertIsNone(self.module.pick_riskbird_link({"cards": []}, "示例市星辰日用品有限公司"))

    def test_reference_documents_riskbird_url_and_conflict_rules(self):
        reference = (ROOT / "references/local-belt-visit.md").read_text(encoding="utf-8")
        for marker in ("entid", "INVALID_REQUEST", "IDENTITY_CONFLICT", "风控", "未披露/未确认",
                       "零付费", "拜访路线"):
            self.assertIn(marker, reference)


if __name__ == "__main__":
    unittest.main()
