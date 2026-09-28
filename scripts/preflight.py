import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import requests
from enrich_companies import decode_mcp_envelopes

from configure_enterprise_keys import (
    FENGNIAO_ENV_NAME,
    QCC_ENV_NAME,
    QCC_MCP_URL,
    ensure_qcc_mcp_config,
    normalize_qcc_auth,
    read_user_environment,
)


ENTERPRISE_TERMS = ("qcc", "企查查", "aiqicha", "爱企查", "tianyancha", "天眼查")
MIN_PYTHON_VERSION = (3, 11)
REQUIRED_PYTHON_PACKAGES = {
    "openpyxl": "openpyxl>=3.1",
    "requests": "requests>=2.32",
}
O2_INDEX_URL = "https://artifactory.jd.com/artifactory/api/pypi/libs-py-local/simple"


def python_runtime_status(version_info=None):
    version = tuple(version_info or sys.version_info[:3])
    return {
        "ok": version >= MIN_PYTHON_VERSION,
        "version": ".".join(str(part) for part in version),
        "minimum": ".".join(str(part) for part in MIN_PYTHON_VERSION),
        "executable": sys.executable,
    }


def python_packages_status():
    importlib.invalidate_caches()
    installed = []
    missing = []
    for module in REQUIRED_PYTHON_PACKAGES:
        (installed if importlib.util.find_spec(module) else missing).append(module)
    return {"ok": not missing, "installed": installed, "missing": missing}


def find_o2():
    direct = shutil.which("o2")
    if direct:
        return direct
    scripts_dir = Path(sys.executable).resolve().parent / "Scripts"
    for name in ("o2.exe", "o2"):
        candidate = scripts_dir / name
        if candidate.is_file():
            return str(candidate)
    return ""


def detect_mcp(config_path):
    if not config_path.exists():
        return []
    config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
    matches = []
    for name, value in config.get("mcp_servers", {}).items():
        text = f"{name} {value}".lower()
        if any(term.lower() in text for term in ENTERPRISE_TERMS):
            matches.append(name)
    return sorted(matches)


def detect_qcc_mcp(config_path):
    server = {}
    if config_path.exists():
        config = tomllib.loads(config_path.read_text(encoding="utf-8-sig"))
        server = config.get("mcp_servers", {}).get("qcc-company", {})
    configured = (
        server.get("url") == QCC_MCP_URL
        and server.get("bearer_token_env_var") == QCC_ENV_NAME
    )
    return {
        "configured": configured,
        "key_configured": bool(read_user_environment(QCC_ENV_NAME)),
        "url": server.get("url", ""),
    }


def detect_fengniao(skill_dir=None):
    path = Path(skill_dir or Path.home() / ".openclaw/skills/company-search-fengniao")
    installed = (path / "scripts/tool.mjs").is_file()
    key_configured = bool(read_user_environment(FENGNIAO_ENV_NAME))
    return {
        "installed": installed,
        "key_configured": key_configured,
        "ready": installed and key_configured,
        "path": str(path),
    }


def run(command, env=None):
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    except OSError as error:
        return 127, "", str(error)


def install_python_dependencies(requirements_path=None):
    path = Path(requirements_path or Path(__file__).resolve().parents[1] / "requirements.txt")
    code, out, err = run([sys.executable, "-m", "pip", "install", "-r", str(path)])
    return code == 0, out or err


def install_o2():
    code, out, err = run([sys.executable, "-m", "pip", "install", "-U", "--index-url", O2_INDEX_URL, "o2"])
    return code == 0, out or err


def install_webcli():
    o2 = find_o2()
    if not o2:
        return False, "o2 not installed"
    code, out, err = run([o2, "install", "webcli"])
    return code == 0, out or err


def install_webcli_extension(extension_dir=None):
    o2 = find_o2()
    if not o2:
        return False, "o2 not installed"
    code, out, err = run([o2, "launch", "webcli", "extension", "install"])
    if code == 0:
        return True, out or err
    path = Path(extension_dir or Path.home() / ".webcli/extension").resolve()
    archive = path.parent / "extension.zip"
    if not archive.is_file():
        return False, err or out
    try:
        path.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (path / member.filename).resolve()
                if path != target and path not in target.parents:
                    return False, "extension archive contains an unsafe path"
            bundle.extractall(path)
        return True, "extension archive extracted"
    except (OSError, zipfile.BadZipFile):
        return False, "extension archive extraction failed"


def install_fengniao():
    command = [
        "npx",
        "-y",
        "openclaw@2026.7.1-2",
        "skills",
        "install",
        "@xinshu001/company-search-fengniao",
        "--global",
        "--acknowledge-clawhub-risk",
    ]
    code, out, err = run(command)
    return code == 0, out or err


def install_qcc(config_path):
    try:
        ensure_qcc_mcp_config(config_path)
        return True, "qcc-company MCP configured with QCC_AUTH reference"
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        return False, f"QCC MCP configuration failed: {type(error).__name__}"


def webcli_doctor():
    o2 = find_o2()
    if not o2:
        return {"ok": False, "error": "o2 missing"}
    code, out, err = run([o2, "launch", "webcli", "--json", "doctor"])
    if code:
        return {"ok": False, "error": err or out}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": out}


def webcli_browser_ready(doctor):
    return bool(
        doctor.get("connectivity", {}).get("ok")
        and any(profile.get("extensionConnected") for profile in doctor.get("profiles", []))
    )


def browser_setup_instructions(extension_dir=None):
    path = Path(extension_dir or Path.home() / ".webcli/extension").resolve()
    return [
        "在 Chrome 地址栏打开 chrome://extensions。",
        "打开右上角“开发者模式”。",
        f"点击“加载已解压的扩展程序”，选择此目录：{path}",
        "把 Browser Bridge 固定到 Chrome 工具栏。",
        "保持 Chrome 开启，然后重新运行 bootstrap.ps1。",
    ]


def validate_qcc(qcc, auth=None):
    token = auth or read_user_environment(QCC_ENV_NAME)
    if not qcc.get("configured") or not token:
        return {"validated": False, "error": "missing_configuration_or_key"}
    try:
        response = requests.post(
            qcc["url"],
            headers={
                "Authorization": normalize_qcc_auth(token),
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            timeout=30,
        )
    except requests.RequestException:
        return {"validated": False, "error": "request_failed"}
    if response.status_code < 200 or response.status_code >= 300:
        return {"validated": False, "error": f"http_{response.status_code}"}
    response.encoding = "utf-8"
    for envelope in decode_mcp_envelopes(response.text):
        if envelope.get("error"):
            return {"validated": False, "error": "provider_rejected_request"}
        result = envelope.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            return {"validated": True, "error": ""}
    return {"validated": False, "error": "invalid_provider_response"}


def validate_fengniao(skill_dir, key=None):
    path = Path(skill_dir)
    token = key or read_user_environment(FENGNIAO_ENV_NAME)
    tool = path / "scripts/tool.mjs"
    if not tool.is_file() or not token:
        return {"validated": False, "error": "missing_installation_or_key"}
    environment = os.environ.copy()
    environment[FENGNIAO_ENV_NAME] = token
    params = json.dumps({"key": "京东"}, ensure_ascii=False)
    code, out, _ = run(
        ["node", str(tool), "call", "biz_fuzzy_search", "--params", params],
        env=environment,
    )
    if code:
        return {"validated": False, "error": "provider_validation_failed"}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {"validated": False, "error": "invalid_provider_response"}
    valid = payload.get("code") == 20000
    return {"validated": valid, "error": "" if valid else "provider_rejected_key_or_quota"}


def enterprise_sources_ready(qcc, fengniao):
    return all(qcc.get(field) for field in ("configured", "key_configured", "validated")) and all(
        fengniao.get(field) for field in ("installed", "key_configured", "validated")
    )


def review_structure_status(root=None):
    skill_root = Path(root or Path(__file__).resolve().parents[1])
    required = [
        "SKILL.md",
        "scripts/review_workbook.py",
        "scripts/audit_review_shops.py",
        "references/review-mode.md",
        "references/review-data-contract.md",
    ]
    missing = [item for item in required if not (skill_root / item).is_file()]
    return {"ok": not missing, "missing": missing}


def build_status(
    *, audit_only, runtime, packages, o2_path, doctor, browser_ready, taobao,
    mcps, qcc, fengniao, actions, review_structure=None, enterprise_only=False,
):
    enterprise_ready = enterprise_sources_ready(qcc, fengniao)
    structure = review_structure or {"ok": True, "missing": []}
    required_ready = (
        runtime["ok"]
        and packages["ok"]
        and (bool(o2_path) or enterprise_only)
        and (browser_ready or enterprise_only)
        and (taobao is None or bool(taobao.get("loggedIn")))
        and (structure["ok"] if audit_only else enterprise_ready)
    )
    result = {
        "ok": required_ready if audit_only else required_ready and enterprise_ready,
        "audit_only": audit_only,
        "enterprise_only": enterprise_only,
        "python": runtime,
        "python_packages": packages,
        "o2": {"installed": bool(o2_path), "path": o2_path},
        "enterprise_mcps": mcps,
        "qcc": qcc,
        "fengniao": fengniao,
        "webcli": {**doctor, "browser_ready": browser_ready},
        "taobao": taobao,
        "review_structure": structure,
        "actions": actions,
        "next": [],
    }
    if not runtime["ok"]:
        result["next"].append("Install Python 3.11 or newer, then rerun scripts/bootstrap.ps1")
    if not packages["ok"]:
        result["next"].append("Install required Python packages from requirements.txt")
    if not o2_path and not enterprise_only:
        result["next"].append("Install o2 with the configured JD Python package index")
    if not audit_only and not enterprise_ready:
        result["next"].extend(
            [
                "必须同时提供并验证企查查 Key 与风鸟 Key，缺一不可。把两个 Key 一起发给 Codex，由 Codex 通过标准输入安全配置；不要自行设置环境变量。",
                "企查查 Key：https://agent.qcc.com/profile/api-key",
                "风鸟 Key：https://www.riskbird.com/center/apiKey",
            ]
        )
    if not browser_ready and not enterprise_only:
        result["next"].extend(browser_setup_instructions())
    if taobao and not taobao.get("loggedIn"):
        result["next"].append("Log in to Taobao in the connected Chrome tab, then rerun preflight")
    if audit_only and not structure["ok"]:
        result["next"].append("Restore missing review-mode Skill files: " + ", ".join(structure["missing"]))
    return result


def taobao_state(session):
    o2 = find_o2()
    if not o2:
        return {"ok": False, "error": "o2 missing"}
    run([o2, "launch", "webcli", "browser", session, "open", "https://www.taobao.com/"])
    script = "(()=>{const t=document.body?document.body.innerText:'';return {url:location.href,title:document.title,risk:/验证码|访问被拒绝|滑动验证/.test(t),loggedIn:/消息|购物车|我的淘宝|已买到的宝贝/.test(t)&&!/亲，请登录/.test(t)}})()"
    code, out, err = run([o2, "launch", "webcli", "browser", session, "eval", script])
    if code:
        return {"ok": False, "error": err or out}
    try:
        return {"ok": True, **json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": False, "error": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path.home() / ".codex/config.toml"))
    parser.add_argument("--check-taobao", action="store_true")
    parser.add_argument("--session", default="top_merchants_preflight")
    parser.add_argument("--fengniao-skill-dir")
    parser.add_argument("--install-missing", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--enterprise-only", action="store_true")
    parser.add_argument("--allow-paid-api", action="store_true")
    args = parser.parse_args()
    if args.enterprise_only and (args.audit_only or args.check_taobao):
        parser.error("--enterprise-only cannot be combined with browser audit options")

    runtime = python_runtime_status()
    packages = python_packages_status()
    if not args.audit_only and not args.allow_paid_api:
        result = {"ok": runtime["ok"] and packages["ok"], "mode": "browser",
                  "status": "local_checks_only", "paid_api_calls": 0, "login": "not_probed",
                  "python": runtime, "python_packages": packages,
                  "next": ["Read browser-task-router and references/browser-enterprise.md; verify the selected authenticated adapter without paid probes."]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["ok"] else 2)
    o2_path = find_o2()
    actions = []

    if args.install_missing and runtime["ok"] and not packages["ok"]:
        ok, detail = install_python_dependencies()
        actions.append({"action": "install_python_dependencies", "ok": ok, "detail": detail})
        packages = python_packages_status()

    if args.install_missing and runtime["ok"] and not o2_path and not args.enterprise_only:
        ok, detail = install_o2()
        actions.append({"action": "install_o2", "ok": ok, "detail": detail})
        o2_path = find_o2()

    doctor = {"ok": True, "skipped": True} if args.enterprise_only else webcli_doctor()
    if not doctor.get("ok") and args.install_missing and o2_path:
        ok, detail = install_webcli()
        actions.append({"action": "install_webcli", "ok": ok, "detail": detail})
        doctor = webcli_doctor()
    if not webcli_browser_ready(doctor) and args.install_missing and o2_path and not args.enterprise_only:
        ok, detail = install_webcli_extension()
        actions.append({"action": "install_webcli_extension", "ok": ok, "detail": detail})
        doctor = webcli_doctor()

    config_path = Path(args.config)
    if args.audit_only:
        mcps = []
        qcc = {"configured": False, "key_configured": False, "validated": False, "skipped": True}
        fengniao = {"installed": False, "key_configured": False, "validated": False, "skipped": True}
    else:
        mcps = detect_mcp(config_path)
        qcc = detect_qcc_mcp(config_path)
        fengniao = detect_fengniao(args.fengniao_skill_dir)
        if not qcc["configured"] and args.install_missing:
            ok, detail = install_qcc(config_path)
            actions.append({"action": "configure_qcc", "ok": ok, "detail": detail})
            mcps = detect_mcp(config_path)
            qcc = detect_qcc_mcp(config_path)
        if not fengniao["installed"] and args.install_missing:
            ok, detail = install_fengniao()
            actions.append({"action": "install_fengniao", "ok": ok, "detail": detail})
            fengniao = detect_fengniao(args.fengniao_skill_dir)

        qcc_validation = validate_qcc(qcc)
        fengniao_validation = validate_fengniao(fengniao["path"])
        qcc["validated"] = qcc_validation["validated"]
        qcc["validation_error"] = qcc_validation["error"]
        fengniao["validated"] = fengniao_validation["validated"]
        fengniao["validation_error"] = fengniao_validation["error"]

    browser_ready = webcli_browser_ready(doctor)
    taobao = taobao_state(args.session) if args.check_taobao and browser_ready else None
    result = build_status(
        audit_only=args.audit_only,
        runtime=runtime,
        packages=packages,
        o2_path=o2_path,
        doctor=doctor,
        browser_ready=browser_ready,
        taobao=taobao,
        mcps=mcps,
        qcc=qcc,
        fengniao=fengniao,
        actions=actions,
        review_structure=review_structure_status() if args.audit_only else None,
        enterprise_only=args.enterprise_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["ok"] else 2)


if __name__ == "__main__":
    main()
