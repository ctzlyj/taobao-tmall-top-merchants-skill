import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import write_json
from enterprise_pipeline import run_pipeline, pipeline_plan


class FixtureProvider:
    def __init__(self, provider, calls, ready=True, conflict=False, fail_once=False):
        self.provider = provider
        self.calls = calls
        self.ready = ready
        self.conflict = conflict
        self.fail_once = fail_once

    def validate(self):
        return self.ready

    def call(self, tool, arguments):
        self.calls.append((self.provider, tool, arguments))
        if self.fail_once and tool == "get_contact_info":
            self.fail_once = False
            raise TimeoutError("fixture timeout")
        company = "样例甲有限公司"
        if tool == "biz_fuzzy_search":
            names = [company] if arguments["key"] in (company, "FIXTURE-A") else [company, "样例乙有限公司"]
            return {"code": 20000, "data": [{"entid": name, "entName": name} for name in names]}
        if tool == "biz_basic_info":
            company = arguments["entid"]
            return {"code": 20000, "data": {"apiData": {
                "entName": company, "uniscid": "CONFLICT" if self.conflict else "FIXTURE-A" if "甲" in company else "FIXTURE-B",
                "dom": "样例注册地址", "personName": "样例法人", "esDate": "2020-01-01",
            }}}
        if tool == "get_company_registration_info":
            query = arguments["searchKey"]
            company = "样例乙有限公司" if "乙" in query else company
            return {"企业名称": company, "统一社会信用代码": "FIXTURE-B" if "乙" in company else "FIXTURE-A"}
        if tool == "get_contact_info":
            return {"联系方式信息": {"电话": [], "邮箱": []}}
        raise AssertionError(tool)


class PipelineExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job_dir = Path(self.temporary.name)
        write_json(self.job_dir / "input_shops.json", [{"shop_name": "样例旗舰店", "shop_url": "https://shop100000000001.taobao.com/"}])
        self.calls = []
        self.qcc = FixtureProvider("qcc", self.calls)
        self.fengniao = FixtureProvider("fengniao", self.calls)

    def add_license(self):
        write_json(self.job_dir / "platform_qualifications.json", {"样例旗舰店": {
            "status": "verified", "evidence_type": "platform_qualification",
            "company_name": "样例甲有限公司", "credit_code": "FIXTURE-A",
            "source_url": "https://example.invalid/platform-evidence",
        }})

    def run_pipeline(self):
        return run_pipeline(self.job_dir, self.qcc, self.fengniao, interval=0)

    def test_dry_plan_has_no_writes_or_provider_calls(self):
        self.add_license()
        before = set(self.job_dir.iterdir())
        plan = pipeline_plan(self.job_dir)
        self.assertEqual(plan["targets"][0]["mode"], "exact_identity")
        self.assertEqual(before, set(self.job_dir.iterdir()))
        self.assertEqual(self.calls, [])

    def test_duplicate_input_rows_keep_distinct_provided_companies(self):
        write_json(self.job_dir / "input_shops.json", [
            {"shop_name": "样例旗舰店", "provided_company": "样例甲有限公司"},
            {"shop_name": "样例旗舰店", "provided_company": "样例乙有限公司"},
        ])
        plan = pipeline_plan(self.job_dir)
        self.assertEqual(plan["count"], 1)
        self.assertEqual({row["company"] for row in plan["targets"][0]["subjects"]}, {"样例甲有限公司", "样例乙有限公司"})

    def test_private_dual_source_gate_runs_before_writes(self):
        self.fengniao.ready = False
        before = set(self.job_dir.iterdir())
        with self.assertRaisesRegex(RuntimeError, "ENTERPRISE_SOURCES_NOT_READY"):
            self.run_pipeline()
        self.assertEqual(before, set(self.job_dir.iterdir()))
        self.assertEqual(self.calls, [])

    def test_license_routes_directly_to_qcc_then_fengniao_and_merges_only_missing_fields(self):
        self.add_license()
        result = self.run_pipeline()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][:2], ("qcc", "get_company_registration_info"))
        self.assertEqual(self.calls[0][2]["searchKey"], "FIXTURE-A")
        saved = json.loads((self.job_dir / "company_enrichment.json").read_text(encoding="utf-8"))
        candidate = saved["样例旗舰店"][0]
        self.assertEqual(candidate["registration"]["注册地址"], "样例注册地址")
        self.assertEqual(candidate["field_sources"]["注册地址"], "fengniao")
        self.assertFalse(candidate["selected"])
        self.assertTrue(candidate["dual_source_verified"])

    def test_fuzzy_route_preserves_all_candidates_without_selecting_first(self):
        result = self.run_pipeline()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.calls[0][:2], ("fengniao", "biz_fuzzy_search"))
        candidates = json.loads((self.job_dir / "company_enrichment.json").read_text(encoding="utf-8"))["样例旗舰店"]
        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(not candidate["selected"] for candidate in candidates))

    def test_identity_conflict_is_persisted_and_stops_batch(self):
        self.add_license()
        self.fengniao.conflict = True
        result = self.run_pipeline()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason"], "identity_conflict")
        self.assertTrue((self.job_dir / "enterprise_checkpoint.json").exists())

    def test_resume_only_retries_failed_step(self):
        self.add_license()
        self.qcc.fail_once = True
        first = self.run_pipeline()
        self.assertEqual(first["status"], "paused")
        second = self.run_pipeline()
        self.assertEqual(second["status"], "completed")
        tools = [call[1] for call in self.calls]
        self.assertEqual(tools.count("get_company_registration_info"), 1)
        self.assertEqual(tools.count("get_contact_info"), 2)

    def test_unchanged_rerun_reuses_provider_results(self):
        self.add_license()
        self.run_pipeline()
        self.calls.clear()
        self.run_pipeline()
        self.assertEqual(self.calls, [])

    def test_changed_license_does_not_reuse_old_decision(self):
        self.add_license()
        self.run_pipeline()
        self.calls.clear()
        path = self.job_dir / "platform_qualifications.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["样例旗舰店"]["credit_code"] = "CHANGED-CODE"
        write_json(path, data)
        result = self.run_pipeline()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason"], "identity_conflict")
        self.assertTrue(self.calls)

    def test_explicit_no_contact_record_is_not_a_batch_failure(self):
        self.add_license()
        original = self.qcc.call

        def no_contact(tool, arguments):
            if tool == "get_contact_info":
                return {"企业名称": "样例甲有限公司", "搜索结果": "已全量扫描该主体联系方式数据库，未发现任何记录。"}
            return original(tool, arguments)

        self.qcc.call = no_contact
        result = self.run_pipeline()
        self.assertEqual(result["status"], "completed")
        saved = json.loads((self.job_dir / "company_enrichment.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["样例旗舰店"][0]["contact"]["contact_status"], "not_disclosed")

    def test_invalid_contact_shape_is_not_reused_as_success(self):
        self.add_license()
        original = self.qcc.call
        attempts = []

        def broken_once(tool, arguments):
            if tool == "get_contact_info":
                attempts.append(tool)
                if len(attempts) == 1:
                    return {"unexpected": "shape"}
            return original(tool, arguments)

        self.qcc.call = broken_once
        self.assertEqual(self.run_pipeline()["status"], "paused")
        self.assertEqual(self.run_pipeline()["status"], "completed")
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
