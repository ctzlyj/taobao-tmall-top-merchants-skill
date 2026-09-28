import json
from pathlib import Path

from enrich_companies import QccClient, load_qcc_server
from preflight import detect_fengniao, detect_qcc_mcp, validate_qcc
from run_fengniao import execute_fengniao


class QccProvider(QccClient):
    def __init__(self, config_path):
        self.configuration = detect_qcc_mcp(Path(config_path))
        url, auth = load_qcc_server(config_path)
        super().__init__(url, auth)

    def validate(self):
        if not self.configuration.get("configured") or not self.configuration.get("key_configured"):
            return False
        return validate_qcc(self.configuration).get("validated") is True


class FengniaoProvider:
    def __init__(self, skill_dir=None):
        self.configuration = detect_fengniao(skill_dir)

    def validate(self):
        if not self.configuration.get("installed") or not self.configuration.get("key_configured"):
            return False
        reply = self.call("biz_fuzzy_search", {"key": "京东"})
        return reply.get("code") == 20000 and isinstance(reply.get("data"), list)

    def call(self, tool, arguments):
        result = execute_fengniao(["call", tool, "--params", json.dumps(arguments, ensure_ascii=False)], self.configuration["path"])
        if result["returncode"]:
            raise RuntimeError("fengniao_provider_unavailable")
        try:
            payload = json.loads(result["stdout"])
        except json.JSONDecodeError:
            raise RuntimeError("fengniao_invalid_response") from None
        if not isinstance(payload, dict):
            raise RuntimeError("fengniao_invalid_response")
        return payload


def configured_providers(config_path):
    return QccProvider(config_path), FengniaoProvider()
