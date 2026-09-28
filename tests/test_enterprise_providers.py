import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from enrich_companies import QccClient
from run_fengniao import execute_fengniao


class ProviderTests(unittest.TestCase):
    def response(self, envelope, sse=False):
        response = Mock()
        response.status_code = 200
        response.text = ("data: " if sse else "") + json.dumps(envelope) + ("\n\n" if sse else "")
        return response

    def test_qcc_accepts_json_and_sse(self):
        for sse in (False, True):
            with self.subTest(sse=sse), patch("enrich_companies.requests.post") as post:
                post.return_value = self.response({"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": json.dumps({"企业名称": "样例有限公司"})}]}}, sse)
                result = QccClient("https://example.invalid/mcp", "fixture-auth-not-a-key").call("get_company_registration_info", {"searchKey": "样例有限公司"})
                self.assertEqual(result["企业名称"], "样例有限公司")

    def test_tool_is_error_cannot_be_cached_as_success(self):
        with patch("enrich_companies.requests.post") as post:
            post.return_value = self.response({"result": {"isError": True, "content": [{"type": "text", "text": json.dumps({"企业名称": "不可使用"})}]}})
            result = QccClient("https://example.invalid/mcp", "fixture-auth-not-a-key").call("get_company_registration_info", {})
            self.assertIn("error", result)

    def test_fengniao_has_bounded_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "scripts").mkdir()
            (path / "scripts/tool.mjs").write_text("", encoding="utf-8")
            runner = Mock(return_value=Mock(returncode=0, stdout="{}", stderr=""))
            execute_fengniao(["call", "biz_basic_info"], path, "fixture-auth-not-a-key", runner)
            self.assertLessEqual(runner.call_args.kwargs["timeout"], 90)


if __name__ == "__main__":
    unittest.main()
