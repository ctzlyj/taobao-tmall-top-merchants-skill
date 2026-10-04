import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LocalBeltDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import sys
        self.modules_backup = dict(sys.modules)
        self.test_dir = tempfile.TemporaryDirectory()
        sys.path.insert(0, str(ROOT / "scripts"))
        import local_belt_discovery
        self.module = local_belt_discovery

    def tearDown(self):
        import sys
        sys.path.remove(str(ROOT / "scripts"))
        for name, module in self.modules_backup.items():
            if sys.modules.get(name) is not module:
                sys.modules[name] = module
        self.test_dir.cleanup()

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

    def test_riskbird_full_name_resolves_card_abbreviation_for_adapter_identity(self):
        search_row = {"cards": [{
            "links": [
                {"name": "示例市闽江商贸有限公司", "url": "https://www.riskbird.com/ent/a.html?entid=one"},
            ]
        }]}
        self.assertEqual(self.module.riskbird_full_name(search_row, "示例市闽江商贸"),
                         "示例市闽江商贸有限公司")
        self.assertEqual(self.module.riskbird_full_name({"cards": []}, "示例市闽江商贸"),
                         "示例市闽江商贸")

    def test_parse_trademark_rows_normalizes_tabs_and_filters_registered_marks(self):
        raw = ("商标信息\n19\n序号\t\n\t商标名称\t\n\t国际分类\t\n\t商标状态\t\n\t申请注册号\n"
               "1\t\n\t梅森生活家\t第21类 厨房洁具\t已注册\t58708495\t2021-08-24\n"
               "2\t\n\t示例图形\t第35类 广告销售\t商标无效\t11111111\t2021-08-02\n"
               "3\t\n\t示例图案\t第21类 厨房洁具\t初审公告\t22222222\t2021-08-02\n")
        rows = self.module.parse_trademark_rows(raw)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["name"], "梅森生活家")
        self.assertEqual(rows[0]["class"], "第21类 厨房洁具")
        self.assertEqual(rows[0]["status"], "已注册")
        self.assertEqual(rows[0]["reg_no"], "58708495")
        self.assertEqual(rows[2]["status"], "初审公告")
        registered = self.module.registered_trademarks(rows)
        self.assertEqual(registered, [rows[0]])
        relevant = self.module.relevant_trademarks(registered)
        self.assertEqual(relevant, [rows[0]])

    def test_relevant_trademark_classes_cover_recruitment_categories(self):
        row = {"name": "示例商标", "class": "第10类 医疗器械", "status": "已注册", "reg_no": "1", "applied": "2021-01-01"}
        self.assertEqual(self.module.relevant_trademarks([row]), [row])
        unrelated = dict(row, **{"class": "第41类 教育娱乐"})
        self.assertEqual(self.module.relevant_trademarks([unrelated]), [])

    def test_reference_documents_riskbird_url_and_conflict_rules(self):
        reference = (ROOT / "references/local-belt-visit.md").read_text(encoding="utf-8")
        for marker in ("entid", "INVALID_REQUEST", "IDENTITY_CONFLICT", "风控", "未披露/未确认",
                       "零付费", "拜访路线"):
            self.assertIn(marker, reference)

    def test_reference_documents_trademark_tab_and_conflict_diagnostics(self):
        reference = (ROOT / "references/local-belt-visit.md").read_text(encoding="utf-8")
        for marker in ("知识产权", "商标", "曾用名", "全称", "table.xs-descriptions-box",
                       "web_searchBrand", "verify.qcc.com/limits"):
            self.assertIn(marker, reference)

    def test_needs_api_fallback_only_when_browser_path_has_no_phone(self):
        self.assertTrue(self.module.needs_api_fallback(None))
        self.assertTrue(self.module.needs_api_fallback({"status": "not_found_in_riskbird"}))
        self.assertTrue(self.module.needs_api_fallback({"status": "adapter_failed"}))
        self.assertTrue(self.module.needs_api_fallback(
            {"status": "ok", "detail": {"phones": [], "contact_status": "not_disclosed"}}))
        self.assertFalse(self.module.needs_api_fallback(
            {"status": "ok", "detail": {"phones": ["13800000000"]}}))
        self.assertFalse(self.module.needs_api_fallback({"status": "ok_conflict_retry",
                                                         "detail": {"phones": ["0516-1234567"]}}))

    def test_api_contact_fallback_queries_only_phoneless_companies(self):
        companies = [{"name": "示例市甲日用品有限公司"}, {"name": "示例市乙制品厂"}]
        details = [{"name": "示例市甲日用品有限公司", "status": "ok",
                    "detail": {"phones": ["13800000000"], "emails": []}}]
        calls = []

        class FakeClient:
            def call(self, name, arguments):
                calls.append((name, arguments))
                return {"企业名称": "示例市乙制品厂", "联系方式信息": {"电话": ["0516-87654321"], "邮箱": []}}

        result = self.module.api_contact_fallback(
            companies, details, FakeClient(), self._temp_path("fallback.json"))
        self.assertEqual(len(result), 1)
        self.assertEqual(calls, [("get_contact_info", {"searchKey": "示例市乙制品厂", "excludeInvalidPhone": True})])
        self.assertEqual(result[0]["name"], "示例市乙制品厂")
        self.assertEqual(result[0]["status"], "ok")
        self.assertEqual(result[0]["phones"], ["0516-87654321"])
        self.assertEqual(result[0]["source"], "qcc_api")

    def test_api_contact_fallback_keeps_conflict_and_nondisclosure(self):
        companies = [{"name": "示例市丙制品厂"}, {"name": "示例市丁商贸有限公司"}]
        details = []

        class FakeClient:
            def __init__(self):
                self.replies = [
                    {"企业名称": "完全不同的公司", "联系方式信息": {"电话": ["13900000000"], "邮箱": []}},
                    {"企业名称": "示例市丁商贸有限公司",
                     "搜索结果": "已全量扫描该主体联系方式数据库，未发现任何记录。",
                     "联系方式信息": None},
                ]

            def call(self, name, arguments):
                return self.replies.pop(0)

        result = self.module.api_contact_fallback(
            companies, details, FakeClient(), self._temp_path("fallback2.json"))
        self.assertEqual(result[0]["status"], "api_identity_conflict")
        self.assertEqual(result[0]["phones"], [])
        self.assertEqual(result[1]["status"], "not_disclosed")
        self.assertEqual(result[1]["phones"], [])

    def test_api_contact_fallback_is_idempotent_for_saved_companies(self):
        companies = [{"name": "示例市乙制品厂"}]
        details = []
        path = self._temp_path("fallback3.json")
        path.write_text(json.dumps([{"name": "示例市乙制品厂", "status": "not_disclosed",
                                     "phones": [], "source": "qcc_api"}], ensure_ascii=False),
                        encoding="utf-8")

        class ExplodingClient:
            def call(self, *_args):
                raise AssertionError("paid API must not be called for saved companies")

        result = self.module.api_contact_fallback(companies, details, ExplodingClient(), path)
        self.assertEqual(result[0]["status"], "not_disclosed")

    def _temp_path(self, filename):
        return Path(self.test_dir.name) / filename


if __name__ == "__main__":
    unittest.main()
