import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from common import write_json
import enterprise_pipeline
import preflight


class PaidGateTests(unittest.TestCase):
    def test_default_execute_never_constructs_paid_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            write_json(Path(directory) / "input_shops.json", [{"shop_name": "示例店"}])
            with patch.object(sys, "argv", ["pipeline", "--job-dir", directory, "--execute"]), \
                    patch("enterprise_providers.configured_providers", side_effect=AssertionError("paid provider called")), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                status = enterprise_pipeline.main()
            result = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(result["mode"], "browser")
            self.assertEqual(result["paid_api_calls"], 0)

    def test_api_execution_requires_explicit_opt_in(self):
        with patch.object(sys, "argv", ["pipeline", "--job-dir", ".", "--mode", "paid-api", "--execute"]), \
                patch("enterprise_providers.configured_providers") as providers, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            enterprise_pipeline.main()
        providers.assert_not_called()

    def test_paid_mode_still_works_with_explicit_opt_in(self):
        with patch.object(sys, "argv", ["pipeline", "--job-dir", ".", "--mode", "paid-api", "--allow-paid-api", "--execute"]), \
                patch("enterprise_providers.configured_providers", return_value=("qcc", "fengniao")), \
                patch.object(enterprise_pipeline, "run_pipeline", return_value={"status": "completed"}) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(enterprise_pipeline.main(), 0)
        run.assert_called_once()

    def test_default_preflight_does_not_probe_keys_or_browser(self):
        with patch.object(sys, "argv", ["preflight"]), \
                patch.object(preflight, "validate_qcc", side_effect=AssertionError("paid probe")), \
                patch.object(preflight, "validate_fengniao", side_effect=AssertionError("paid probe")), \
                patch.object(preflight, "webcli_doctor", side_effect=AssertionError("browser touched")), \
                patch.object(preflight, "detect_mcp", side_effect=AssertionError("credentials loaded")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit_context:
                preflight.main()
        self.assertEqual(exit_context.exception.code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["paid_api_calls"], 0)
        self.assertEqual(result["login"], "not_probed")

    def test_legacy_commands_require_paid_opt_in_before_loading_credentials(self):
        import enrich_companies
        import run_fengniao
        for module, arguments, protected in (
            (enrich_companies, ["enrich", "--job-dir", "."], "load_qcc_server"),
            (run_fengniao, ["call", "biz_fuzzy_search"], "execute_fengniao"),
        ):
            with self.subTest(module=module.__name__), patch.object(sys, "argv", [module.__name__, *arguments]), \
                    patch.object(module, protected) as call, contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                module.main()
            call.assert_not_called()


class BrowserRoutingContractTests(unittest.TestCase):
    def test_router_and_paid_execution_boundaries_are_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "references/browser-enterprise.md").read_text(encoding="utf-8")
        self.assertIn("browser-task-router/SKILL.md", skill)
        self.assertIn("--mode paid-api --allow-paid-api", skill)
        self.assertNotIn("优先O2现有浏览器", reference)
        for marker in ("Browser-Use", "Playwright", "browser_targets.json", "browser_cache.json", "needs_browser_discovery"):
            self.assertIn(marker, reference)

    def test_bootstrap_paid_flag_is_explicit(self):
        script = (ROOT / "scripts/bootstrap.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$AllowPaidApi", script)
        self.assertIn("if ($AllowPaidApi)", script)
        self.assertIn('"--allow-paid-api"', script)


class BrowserExecutionTests(unittest.TestCase):
    def setUp(self):
        import browser_enterprise
        self.module = browser_enterprise
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name)
        self.target = {"shop_name": "示例店", "company": "示例企业有限公司",
                       "credit_code": "913100000000000001",
                       "source_url": "https://www.riskbird.com/ent/example.html?entid=example"}
        self.record = {
            **self.target, "registration_number": "310000000000001", "legal_name": "示例法人",
            "address": "示例地址", "phones": ["010-12345678", "010-12345679"], "emails": [],
            "contact_status": "visible_records", "identity_verified": True, "source": "riskbird_web",
            "captured_at": enterprise_pipeline.now_text(), "paid_api_calls": 0,
        }
        self.provider = Mock()
        self.provider.read.return_value = self.record
        self.save_targets()

    def save_targets(self):
        write_json(self.job / "browser_targets.json", [self.target])

    def execute(self):
        return self.module.run_browser_pipeline(self.job, execute=True, provider=self.provider, interval=0)

    def evidence(self, person="张经理", phone="010-12345678"):
        return {"phone": phone, "person": person, "role": "商务联系人", "company": self.target["company"],
                "source_url": "https://example.invalid/contact", "captured_at": enterprise_pipeline.now_text(),
                "excerpt": f"商务联系人：{person}；电话：{phone}", "association": "explicit"}

    def test_plan_is_read_only(self):
        before = set(self.job.iterdir())
        with patch.object(self.module, "RiskbirdWebProvider", side_effect=AssertionError("browser launched")):
            result = self.module.run_browser_pipeline(self.job)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(before, set(self.job.iterdir()))

    def test_legal_name_is_not_phone_user(self):
        result = self.execute()
        self.assertEqual(result["status"], "completed")
        for contact in result["results"][0]["contacts"]:
            self.assertEqual(contact["person"], "未披露/未确认")
            self.assertNotIn("示例法人", contact["opening"])
        self.assertFalse(result["results"][0]["dual_source_verified"])

    def test_evidence_is_per_number(self):
        self.target["contact_evidence"] = [self.evidence()]
        self.save_targets()
        contacts = self.execute()["results"][0]["contacts"]
        self.assertEqual(contacts[0]["person"], "张经理")
        self.assertEqual(contacts[0]["confirmation"], "来源明确标注（未拨通）")
        self.assertEqual(contacts[1]["person"], "未披露/未确认")

    def test_conflicting_people_keep_both_sources(self):
        self.target["contact_evidence"] = [self.evidence(), self.evidence("示例乙")]
        self.save_targets()
        contact = self.execute()["results"][0]["contacts"][0]
        self.assertEqual(contact["confirmation"], "来源冲突，暂停认定")
        self.assertEqual(len(contact["evidence"]), 2)

    def test_legal_field_is_not_contact_evidence(self):
        evidence = self.evidence()
        evidence["association"] = "legal_representative"
        self.target["contact_evidence"] = [evidence]
        self.save_targets()
        self.assertEqual(self.execute()["results"][0]["contacts"][0]["person"], "未披露/未确认")

    def test_identity_conflict_pauses(self):
        self.provider.read.return_value = {**self.record, "credit_code": "913100000000000002"}
        result = self.execute()
        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["reason"], "IDENTITY_CONFLICT")
        self.assertEqual(result["results"], [])

    def test_missing_identity_requires_discovery(self):
        self.target.pop("credit_code")
        self.save_targets()
        self.assertEqual(self.execute()["status"], "needs_browser_discovery")
        self.provider.read.assert_not_called()

    def test_credentials_in_url_are_rejected(self):
        self.target["source_url"] += "&token=do-not-save"
        self.save_targets()
        with self.assertRaisesRegex(ValueError, "INVALID_SOURCE_URL"):
            self.execute()
        self.provider.read.assert_not_called()

    def test_resume_and_changed_target(self):
        self.execute()
        self.assertEqual(self.execute()["cache_hits"], 1)
        self.provider.read.assert_called_once()
        self.target["legal_name"] = "示例法人"
        self.save_targets()
        self.execute()
        self.assertEqual(self.provider.read.call_count, 2)

    def test_unknown_structure_is_not_absence(self):
        self.provider.read.return_value = {**self.record, "contact_status": "unknown"}
        result = self.execute()
        self.assertEqual(result["reason"], "UNRECOGNIZED_CONTACT")
        self.assertEqual(result["results"], [])

    def test_masked_numbers_are_not_filled(self):
        self.provider.read.return_value = {**self.record, "phones": [], "contact_status": "masked"}
        result = self.execute()["results"][0]
        self.assertEqual(result["contact_status"], "masked")
        self.assertEqual(result["contacts"], [])

    def test_challenge_stops_without_retry(self):
        self.provider.read.side_effect = self.module.BrowserStopped("CHALLENGE_REQUIRED")
        self.assertEqual(self.execute()["status"], "paused")
        self.provider.read.assert_called_once()

    def test_timeout_is_sanitized_and_retryable(self):
        self.provider.read.side_effect = TimeoutError("Cookie=do-not-save")
        result = self.execute()
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("do-not-save", json.dumps(result))
        self.provider.read.side_effect = None
        self.assertEqual(self.execute()["status"], "completed")

    def test_company_conflict_in_phone_evidence_is_not_accepted(self):
        evidence = self.evidence()
        evidence["company"] = "其他企业"
        self.target["contact_evidence"] = [evidence]
        self.save_targets()
        self.assertEqual(self.execute()["results"][0]["contacts"][0]["confirmation"], "来源冲突，暂停认定")

    def test_explicit_marker_does_not_override_legal_only_excerpt(self):
        evidence = self.evidence()
        evidence.update(excerpt="法人：张经理；企业电话：010-12345678", role="")
        self.target["contact_evidence"] = [evidence]
        self.save_targets()
        self.assertEqual(self.execute()["results"][0]["contacts"][0]["person"], "未披露/未确认")

    def test_expired_phone_evidence_does_not_assign_person(self):
        evidence = self.evidence()
        evidence["captured_at"] = "2000-01-01T00:00:00+00:00"
        self.target["contact_evidence"] = [evidence]
        self.save_targets()
        self.assertEqual(self.execute()["results"][0]["contacts"][0]["person"], "未披露/未确认")

    def test_provided_address_is_preserved_as_conflict_not_identity_failure(self):
        self.target["address"] = "截图旧地址"
        self.save_targets()
        record = self.execute()["results"][0]
        self.assertEqual(record["field_conflicts"]["address"], {"provided": "截图旧地址", "web": "示例地址"})

    def test_limited_run_reports_incomplete_scope(self):
        write_json(self.job / "browser_targets.json", [self.target, {**self.target, "shop_name": "另一示例店"}])
        result = self.module.run_browser_pipeline(self.job, execute=True, provider=self.provider, interval=0, limit=1)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["total_count"], 2)
        self.assertFalse(result["scope_complete"])

    def test_ordinary_failure_does_not_skip_remaining_targets(self):
        write_json(self.job / "browser_targets.json", [self.target, {**self.target, "shop_name": "另一示例店"}])
        self.provider.read.side_effect = [TimeoutError(), self.record]
        result = self.execute()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["results"]), 1)
        self.assertFalse(result["scope_complete"])

    def test_paid_and_contradictory_empty_payloads_are_rejected(self):
        for payload in ({**self.record, "paid_api_calls": 1}, {**self.record, "contact_status": "not_disclosed"}):
            with self.subTest(payload=payload["contact_status"]):
                self.provider.read.return_value = payload
                result = self.execute()
                self.assertEqual(result["status"], "paused")
                self.assertEqual(result["results"], [])

    def test_runtime_uses_literal_argv_background_and_no_error_leak(self):
        target = self.module.normalize_target({**self.target, "company": "示例A&B有限公司"})
        response = Mock(returncode=0, stdout=json.dumps({"data": [self.record]}), stderr="")
        with patch.object(self.module, "webcli_command", return_value=["node", "webcli.js"]), \
                patch.object(self.module.subprocess, "run", return_value=response) as run:
            self.module.RiskbirdWebProvider().read(target)
        arguments = run.call_args.args[0]
        self.assertIn("示例A&B有限公司", arguments)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.kwargs["env"]["WEBCLI_WINDOW"], "background")
        self.assertNotIn("NODE_OPTIONS", run.call_args.kwargs["env"])

    def test_runtime_rejects_failed_envelope(self):
        target = self.module.normalize_target(self.target)
        response = Mock(returncode=0, stdout=json.dumps({"ok": False, "data": [self.record]}), stderr="")
        with patch.object(self.module, "webcli_command", return_value=["node", "webcli.js"]), \
                patch.object(self.module.subprocess, "run", return_value=response), \
                self.assertRaises(self.module.BrowserStopped):
            self.module.RiskbirdWebProvider().read(target)

    def test_paused_changed_target_does_not_erase_other_successful_cache(self):
        second = {**self.target, "shop_name": "另一示例店"}
        write_json(self.job / "browser_targets.json", [self.target, second])
        self.execute()
        write_json(self.job / "browser_targets.json", [{**self.target, "legal_name": "不匹配法人"}, second])
        self.assertEqual(self.execute()["status"], "paused")
        write_json(self.job / "browser_targets.json", [self.target, second])
        self.assertEqual(self.execute()["cache_hits"], 2)
        self.assertEqual(self.provider.read.call_count, 3)


if __name__ == "__main__":
    unittest.main()
