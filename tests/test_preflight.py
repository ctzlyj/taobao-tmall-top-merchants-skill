import os
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preflight
from preflight import detect_fengniao, detect_mcp


class PreflightTests(unittest.TestCase):
    def test_enterprise_only_does_not_require_browser_or_o2(self):
        result = preflight.build_status(
            audit_only=False, enterprise_only=True, runtime={"ok": True}, packages={"ok": True},
            o2_path=None, doctor={"ok": False}, browser_ready=False, taobao=None, mcps=[],
            qcc={"configured": True, "key_configured": True, "validated": True},
            fengniao={"installed": True, "key_configured": True, "validated": True}, actions=[],
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["next"])

    def test_detects_qcc_and_other_enterprise_mcp(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.qcc-company]\nurl="https://agent.qcc.com/mcp/company/stream"\n'
                '[mcp_servers.aiqicha]\nurl="https://example.com/aiqicha"\n',
                encoding="utf-8",
            )
            self.assertEqual(detect_mcp(config), ["aiqicha", "qcc-company"])

    def test_requires_exact_qcc_company_mcp_with_environment_token_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.aiqicha]\nurl="https://example.com/aiqicha"\n'
                '[mcp_servers.qcc-copy]\nurl="https://agent.qcc.com/mcp/company/stream"\n',
                encoding="utf-8",
            )
            self.assertFalse(preflight.detect_qcc_mcp(config)["configured"])

            config.write_text(
                '[mcp_servers.qcc-company]\n'
                'url="https://agent.qcc.com/mcp/company/stream"\n'
                'bearer_token_env_var="QCC_AUTH"\n',
                encoding="utf-8",
            )
            self.assertTrue(preflight.detect_qcc_mcp(config)["configured"])

    def test_detects_fengniao_skill_and_temporary_key(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "company-search-fengniao"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "scripts" / "tool.mjs").write_text("", encoding="utf-8")
            with patch.dict("os.environ", {"FN_API_KEY": "temporary-test-key"}, clear=False):
                result = detect_fengniao(skill_dir)

            self.assertTrue(result["installed"])
            self.assertTrue(result["key_configured"])
            self.assertTrue(result["ready"])
            self.assertNotIn("temporary-test-key", str(result))

    def test_fengniao_public_quota_is_not_ready_without_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory) / "company-search-fengniao"
            (skill_dir / "scripts").mkdir(parents=True)
            (skill_dir / "scripts" / "tool.mjs").write_text("", encoding="utf-8")
            with patch("preflight.read_user_environment", return_value=""):
                result = detect_fengniao(skill_dir)

            self.assertTrue(result["installed"])
            self.assertFalse(result["key_configured"])
            self.assertFalse(result["ready"])

    def test_both_enterprise_sources_must_be_configured_and_valid(self):
        qcc = {"configured": True, "key_configured": True, "validated": True}
        fengniao = {"installed": True, "key_configured": True, "validated": True}

        self.assertTrue(preflight.enterprise_sources_ready(qcc, fengniao))
        for field in ("configured", "key_configured", "validated"):
            blocked_qcc = {**qcc, field: False}
            self.assertFalse(preflight.enterprise_sources_ready(blocked_qcc, fengniao))
        for field in ("installed", "key_configured", "validated"):
            blocked_fengniao = {**fengniao, field: False}
            self.assertFalse(preflight.enterprise_sources_ready(qcc, blocked_fengniao))

    @patch("preflight.requests.post")
    def test_qcc_validation_is_lightweight_and_sanitized(self, post):
        response = Mock(status_code=200, text='data: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}')
        post.return_value = response

        result = preflight.validate_qcc(
            {"configured": True, "url": "https://agent.qcc.com/mcp/company/stream"},
            auth="Bearer sample-qcc-value",
        )

        self.assertTrue(result["validated"])
        self.assertNotIn("sample-qcc-value", str(result))
        self.assertEqual(post.call_args.kwargs["json"]["method"], "tools/list")

    @patch("preflight.requests.post")
    def test_qcc_http_200_without_tools_payload_is_not_ready(self, post):
        for text in ("", "<html>login required</html>", '{"result":{}}'):
            post.return_value = Mock(status_code=200, text=text)
            result = preflight.validate_qcc({"configured": True, "url": "https://example.invalid"}, auth="fixture-auth")
            self.assertFalse(result["validated"])

    @patch("preflight.requests.post")
    def test_qcc_sse_without_charset_is_decoded_as_utf8(self, post):
        response = preflight.requests.Response()
        response.status_code = 200
        response.encoding = "ISO-8859-1"
        response._content = ('data: ' + json.dumps({"result": {"tools": [{"name": "test", "description": "内网企业工具"}]}}, ensure_ascii=False)).encode("utf-8")
        post.return_value = response
        result = preflight.validate_qcc({"configured": True, "url": "https://example.invalid"}, auth="fixture-auth")
        self.assertTrue(result["validated"])

    def test_qcc_direct_client_accepts_raw_or_prefixed_token(self):
        from enrich_companies import QccClient, load_qcc_server

        raw = QccClient("https://example.com", "sample-qcc-value")
        prefixed = QccClient("https://example.com", "Bearer sample-qcc-value")

        self.assertEqual(raw.headers["Authorization"], "Bearer sample-qcc-value")
        self.assertEqual(prefixed.headers["Authorization"], "Bearer sample-qcc-value")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.qcc-company]\nurl="https://example.com"\nbearer_token_env_var="QCC_AUTH"\n',
                encoding="utf-8",
            )
            with patch("enrich_companies.read_user_environment", return_value="sample-qcc-value"):
                self.assertEqual(load_qcc_server(config), ("https://example.com", "sample-qcc-value"))

    def test_fengniao_wrapper_injects_persisted_key_and_redacts_output(self):
        from run_fengniao import execute_fengniao

        completed = Mock(returncode=0, stdout='{"debug":"sample-fengniao-value","code":20000}', stderr="")
        runner = Mock(return_value=completed)
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory)
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "tool.mjs").write_text("", encoding="utf-8")
            result = execute_fengniao(
                ["discover", "企业基本信息"],
                skill_dir=skill_dir,
                key="sample-fengniao-value",
                runner=runner,
            )

        self.assertEqual(result["returncode"], 0)
        self.assertNotIn("sample-fengniao-value", result["stdout"])
        self.assertEqual(runner.call_args.kwargs["env"]["FN_API_KEY"], "sample-fengniao-value")

    @patch("preflight.run", return_value=(0, '{"code":20000,"data":[]}', ""))
    def test_fengniao_validation_uses_private_key_without_exposing_it(self, run):
        with tempfile.TemporaryDirectory() as directory:
            skill_dir = Path(directory)
            (skill_dir / "scripts").mkdir()
            (skill_dir / "scripts" / "tool.mjs").write_text("", encoding="utf-8")
            result = preflight.validate_fengniao(skill_dir, key="sample-fengniao-value")

        self.assertTrue(result["validated"])
        self.assertNotIn("sample-fengniao-value", str(result))
        self.assertEqual(
            run.call_args.args[0],
            [
                "node",
                str(skill_dir / "scripts" / "tool.mjs"),
                "call",
                "biz_fuzzy_search",
                "--params",
                '{"key": "京东"}',
            ],
        )
        self.assertEqual(run.call_args.kwargs["env"]["FN_API_KEY"], "sample-fengniao-value")

    def test_webcli_requires_connectivity_and_a_connected_profile(self):
        disconnected = {
            "ok": True,
            "connectivity": {"ok": True},
            "profiles": [{"extensionConnected": False}],
        }
        connected = {
            "ok": True,
            "connectivity": {"ok": True},
            "profiles": [{"extensionConnected": True}],
        }

        self.assertFalse(preflight.webcli_browser_ready(disconnected))
        self.assertTrue(preflight.webcli_browser_ready(connected))

    def test_browser_setup_instructions_are_beginner_friendly(self):
        extension_dir = Path("C:/Users/example/.webcli/extension")
        steps = "\n".join(preflight.browser_setup_instructions(extension_dir))

        self.assertIn("chrome://extensions", steps)
        self.assertIn("开发者模式", steps)
        self.assertIn("加载已解压的扩展程序", steps)
        self.assertIn(str(extension_dir), steps)
        self.assertIn("固定", steps)
        self.assertIn("保持 Chrome 开启", steps)

    def test_python_runtime_requires_311_or_newer(self):
        self.assertFalse(preflight.python_runtime_status((3, 10, 9))["ok"])
        self.assertTrue(preflight.python_runtime_status((3, 11, 0))["ok"])

    def test_python_packages_report_missing_dependencies(self):
        def fake_find_spec(name):
            return object() if name == "requests" else None

        with patch("preflight.importlib.util.find_spec", side_effect=fake_find_spec):
            result = preflight.python_packages_status()

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], ["openpyxl"])

    @patch("preflight.run", return_value=(0, "installed", ""))
    def test_o2_install_uses_internal_package_index(self, run):
        ok, detail = preflight.install_o2()

        self.assertTrue(ok)
        self.assertEqual(detail, "installed")
        self.assertEqual(
            run.call_args.args[0],
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-U",
                "--index-url",
                "https://artifactory.jd.com/artifactory/api/pypi/libs-py-local/simple",
                "o2",
            ],
        )

    @patch("preflight.find_o2", return_value="o2")
    @patch("preflight.run", return_value=(0, "installed", ""))
    def test_webcli_extension_install_uses_official_command(self, run, _find_o2):
        ok, detail = preflight.install_webcli_extension()

        self.assertTrue(ok)
        self.assertEqual(detail, "installed")
        self.assertEqual(run.call_args.args[0], ["o2", "launch", "webcli", "extension", "install"])

    @patch("preflight.find_o2", return_value="o2")
    @patch("preflight.run", return_value=(1, "", "unzip unavailable"))
    def test_webcli_extension_install_falls_back_to_python_zip_extraction(self, _run, _find_o2):
        with tempfile.TemporaryDirectory() as directory:
            extension_dir = Path(directory) / "extension"
            archive = extension_dir.parent / "extension.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("manifest.json", "{}")

            ok, detail = preflight.install_webcli_extension(extension_dir)

            self.assertTrue(ok)
            self.assertEqual(detail, "extension archive extracted")
            self.assertTrue((extension_dir / "manifest.json").is_file())

    @patch("preflight.run", return_value=(0, "installed", ""))
    def test_fengniao_install_uses_official_package(self, run):
        ok, detail = preflight.install_fengniao()

        self.assertTrue(ok)
        self.assertEqual(detail, "installed")
        self.assertEqual(
            run.call_args.args[0],
            [
                "npx",
                "-y",
                "openclaw@2026.7.1-2",
                "skills",
                "install",
                "@xinshu001/company-search-fengniao",
                "--global",
                "--acknowledge-clawhub-risk",
            ],
        )

    def test_secure_helper_normalizes_and_never_writes_keys_to_config(self):
        from configure_enterprise_keys import configure_enterprise_keys, normalize_qcc_auth, normalize_qcc_key

        self.assertEqual(normalize_qcc_key("sample-qcc-value"), "sample-qcc-value")
        self.assertEqual(normalize_qcc_key("bearer sample-qcc-value"), "sample-qcc-value")
        self.assertEqual(normalize_qcc_auth("sample-qcc-value"), "Bearer sample-qcc-value")
        self.assertEqual(normalize_qcc_auth("bearer sample-qcc-value"), "Bearer sample-qcc-value")
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            persisted = Mock()
            with patch.dict(os.environ, {}, clear=True):
                result = configure_enterprise_keys(
                    "sample-qcc-value",
                    "sample-fengniao-value",
                    config_path=config,
                    persist=persisted,
                )
            config_text = config.read_text(encoding="utf-8")

        self.assertEqual(persisted.call_count, 2)
        self.assertEqual(
            persisted.call_args_list,
            [call("QCC_AUTH", "sample-qcc-value"), call("FN_API_KEY", "sample-fengniao-value")],
        )
        self.assertIn('bearer_token_env_var = "QCC_AUTH"', config_text)
        self.assertNotIn("sample-qcc-value", config_text)
        self.assertNotIn("sample-fengniao-value", config_text)
        self.assertNotIn("sample-qcc-value", str(result))
        self.assertNotIn("sample-fengniao-value", str(result))

    def test_secure_helper_removes_legacy_static_authorization(self):
        from configure_enterprise_keys import ensure_qcc_mcp_config

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.toml"
            config.write_text(
                '[mcp_servers.keep]\nurl = "https://example.com/mcp"\n\n'
                '[mcp_servers.qcc-company]\nurl = "https://agent.qcc.com/mcp/company/stream"\n\n'
                '[mcp_servers.qcc-company.http_headers]\nAuthorization = "legacy-placeholder"\n',
                encoding="utf-8",
            )
            ensure_qcc_mcp_config(config)
            config_text = config.read_text(encoding="utf-8")

        self.assertIn("[mcp_servers.keep]", config_text)
        self.assertNotIn("legacy-placeholder", config_text)
        self.assertNotIn("http_headers", config_text)

    def test_secure_helper_accepts_credentials_only_from_stdin(self):
        source = (ROOT / "scripts" / "configure_enterprise_keys.py").read_text(encoding="utf-8")

        self.assertNotIn("--qcc-key", source)
        self.assertNotIn("--fengniao-key", source)
        self.assertIn("sys.stdin", source)

    def test_main_refuses_missing_key_and_prints_both_official_links(self):
        connected = {
            "ok": True,
            "connectivity": {"ok": True},
            "profiles": [{"extensionConnected": True}],
        }
        qcc = {"configured": True, "key_configured": False, "url": "https://agent.qcc.com/mcp/company/stream"}
        fengniao = {"installed": True, "key_configured": True, "ready": True, "path": "fengniao"}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            sys, "argv", ["preflight.py", "--allow-paid-api", "--config", str(Path(directory) / "config.toml")]
        ), patch("preflight.python_runtime_status", return_value={"ok": True}), patch(
            "preflight.python_packages_status", return_value={"ok": True}
        ), patch("preflight.find_o2", return_value="o2"), patch(
            "preflight.webcli_doctor", return_value=connected
        ), patch("preflight.detect_mcp", return_value=[]), patch(
            "preflight.detect_qcc_mcp", return_value=qcc
        ), patch("preflight.detect_fengniao", return_value=fengniao), patch(
            "preflight.validate_qcc", return_value={"validated": False, "error": "missing_configuration_or_key"}
        ), patch(
            "preflight.validate_fengniao", return_value={"validated": True, "error": ""}
        ), patch("builtins.print") as output:
            with self.assertRaises(SystemExit) as exit_result:
                preflight.main()

        self.assertEqual(exit_result.exception.code, 2)
        payload = json.loads(output.call_args.args[0])
        self.assertFalse(payload["ok"])
        self.assertIn("https://agent.qcc.com/profile/api-key", "\n".join(payload["next"]))
        self.assertIn("https://www.riskbird.com/center/apiKey", "\n".join(payload["next"]))

    def test_bootstrap_script_installs_python_then_runs_preflight(self):
        script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8")

        self.assertIn("Python.Python.3.11", script)
        self.assertIn("--install-missing", script)
        self.assertIn("ConfigureEnterpriseKeys", script)
        self.assertIn("configure_enterprise_keys.py", script)

    def test_audit_only_does_not_require_enterprise_sources(self):
        result = preflight.build_status(
            audit_only=True,
            runtime={"ok": True},
            packages={"ok": True},
            o2_path="o2",
            doctor={"ok": True},
            browser_ready=True,
            taobao={"ok": True, "loggedIn": True},
            mcps=[],
            qcc={"configured": False, "validated": False},
            fengniao={"installed": False, "validated": False},
            actions=[],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["audit_only"])
        self.assertNotIn("企查查 Key", "\n".join(result["next"]))

    def test_normal_mode_still_requires_both_enterprise_sources(self):
        result = preflight.build_status(
            audit_only=False,
            runtime={"ok": True},
            packages={"ok": True},
            o2_path="o2",
            doctor={"ok": True},
            browser_ready=True,
            taobao=None,
            mcps=[],
            qcc={"configured": False, "validated": False},
            fengniao={"installed": False, "validated": False},
            actions=[],
        )
        self.assertFalse(result["ok"])
        self.assertIn("企查查 Key", "\n".join(result["next"]))

    def test_bootstrap_rejects_audit_only_with_skip_taobao(self):
        script = (ROOT / "scripts" / "bootstrap.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[switch]$AuditOnly", script)
        self.assertIn("-AuditOnly and -SkipTaobaoCheck cannot be combined", script)
        self.assertIn('"--audit-only"', script)

if __name__ == "__main__":
    unittest.main()
