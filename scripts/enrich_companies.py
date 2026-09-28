import argparse
import json
import re
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import requests

from common import ensure_job_dir, write_json
from configure_enterprise_keys import QCC_ENV_NAME, normalize_qcc_auth, read_user_environment
from subject_identity import normalized_code, normalized_name


def normalize_contact_reply(reply):
    if not isinstance(reply, dict):
        return reply
    if reply.get("企业名称") and reply.get("搜索结果") == "已全量扫描该主体联系方式数据库，未发现任何记录。":
        return {**reply, "联系方式信息": {"电话": [], "邮箱": []}, "contact_status": "not_disclosed"}
    return reply


def successful_step(value, field):
    if not isinstance(value, dict) or value.get("error") or value.get("raw") or value.get("isError"):
        return False
    if field == "registration":
        return bool(value.get("企业名称"))
    return isinstance(value.get("联系方式信息"), dict)


def fresh_step(record, field, now=None):
    metadata = record.get("lookup", {}).get(field, {})
    if metadata.get("status") != "success" or not successful_step(record.get(field), field):
        return False
    try:
        captured = datetime.fromisoformat(metadata["captured_at"])
        age = ((now or datetime.now(timezone.utc)) - captured).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= age <= (7 * 86400 if field == "registration" else 86400)


def validate_registration(subject, registration):
    expected_code = normalized_code(subject.get("credit_code"))
    actual_code = normalized_code(registration.get("统一社会信用代码"))
    if expected_code and actual_code and expected_code != actual_code:
        raise RuntimeError("ENTERPRISE_IDENTITY_CONFLICT: credit code mismatch")
    company = subject.get("company", "")
    if company and normalized_name(company) != normalized_name(registration.get("企业名称")):
        raise RuntimeError("ENTERPRISE_IDENTITY_CONFLICT: company name mismatch")


def load_qcc_server(config_path, server_name="qcc-company"):
    config = tomllib.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    server = config.get("mcp_servers", {}).get(server_name)
    if not server:
        raise RuntimeError(f"{server_name} MCP is not configured; run preflight.py")
    auth = read_user_environment(QCC_ENV_NAME)
    if not auth:
        auth = server.get("http_headers", {}).get("Authorization", "")
    if not auth:
        raise RuntimeError("QCC authorization missing; set QCC_AUTH or configure local MCP headers")
    return server["url"], auth


def decode_mcp_envelopes(text):
    try:
        envelopes = [json.loads(text.strip())]
    except json.JSONDecodeError:
        envelopes = []
        for event in text.strip().replace("\r\n", "\n").split("\n\n"):
            parts = [line[5:].lstrip() for line in event.splitlines() if line.startswith("data:")]
            if parts:
                try:
                    envelopes.append(json.loads("\n".join(parts)))
                except json.JSONDecodeError:
                    continue
    return [envelope for envelope in envelopes if isinstance(envelope, dict)]


class QccClient:
    def __init__(self, url, auth):
        self.url = url
        self.headers = {"Authorization": normalize_qcc_auth(auth), "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        self.request_id = 0

    def call(self, name, arguments):
        self.request_id += 1
        response = requests.post(self.url, headers=self.headers, json={"jsonrpc": "2.0", "id": self.request_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}, timeout=90)
        response.raise_for_status()
        response.encoding = "utf-8"
        for envelope in decode_mcp_envelopes(response.text):
            if "error" in envelope:
                error = envelope["error"]
                if error.get("code") == 300008:
                    raise RuntimeError("QCC credit balance insufficient; recharge or switch token")
                return {"error": error}
            result = envelope.get("result", {})
            if not isinstance(result, dict):
                continue
            if result.get("isError"):
                return {"error": {"code": "tool_error"}}
            if isinstance(result.get("structuredContent"), dict):
                return result["structuredContent"]
            for item in result.get("content", []):
                if item.get("type") == "text":
                    try:
                        return json.loads(item["text"])
                    except json.JSONDecodeError:
                        return {"error": {"code": "invalid_tool_payload"}}
        return {"error": {"code": "invalid_mcp_response"}}


def brand_query(shop_name):
    value = shop_name
    suffixes = ["官方旗舰店", "旗舰店", "专卖店", "专营店", "品牌店", "官方企业店", "工厂企业店", "企业店", "工厂店", "直营店", "淘宝店", "店"]
    for suffix in suffixes:
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    value = re.sub(r"\s+", "", value)
    return value or shop_name


def discover(job_dir, client, interval):
    audit = json.loads((job_dir / "assortment_audit.json").read_text(encoding="utf-8"))
    output_path = job_dir / "company_candidates.json"
    output = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
    targets = [row for row in audit.values() if row.get("passes_minimum")]
    for index, row in enumerate(targets, 1):
        shop = row["shop_name"]
        if shop in output:
            print(f"[{index}/{len(targets)}] cached {shop}", flush=True)
            continue
        query = brand_query(shop)
        result = client.call("get_company_by_query", {"searchKey": query})
        output[shop] = {"query": query, "result": result}
        write_json(output_path, output)
        companies = result.get("企业信息", []) if isinstance(result, dict) else []
        print(f"[{index}/{len(targets)}] {shop}: {[(x.get('企业名称'), x.get('状态')) for x in companies[:5]]}", flush=True)
        if index < len(targets):
            time.sleep(interval)


def load_subjects(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = []
        for shop, value in data.items():
            if isinstance(value, str):
                rows.append({"shop_name": shop, "company": value})
            elif isinstance(value, dict):
                rows.append({"shop_name": shop, **value})
        return rows
    return data


def enrich(job_dir, client, subjects_path, interval):
    subjects = load_subjects(subjects_path)
    output_path = job_dir / "company_enrichment.json"
    output = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
    cache = {item.get("company"): item for values in output.values() for item in values if item.get("company")}
    for index, subject in enumerate(subjects, 1):
        shop = subject["shop_name"]
        company = subject["company"]
        output.setdefault(shop, [])
        cached = cache.get(company, {})
        record = {key: value for key, value in subject.items() if key not in {"registration", "contact", "lookup"}}
        record.setdefault("selected", False)
        record["lookup"] = {}
        for field, tool in (("registration", "get_company_registration_info"), ("contact", "get_contact_info")):
            if field == "contact" and not successful_step(record.get("registration"), "registration"):
                record[field] = {"error": {"code": "registration_not_verified"}}
                record["lookup"][field] = {"status": "pending"}
                break
            if fresh_step(cached, field):
                record[field] = cached[field]
                record["lookup"][field] = cached["lookup"][field]
            else:
                arguments = {"searchKey": subject.get("credit_code") or company}
                if field == "contact":
                    arguments["excludeInvalidPhone"] = True
                try:
                    record[field] = client.call(tool, arguments)
                    if field == "contact":
                        record[field] = normalize_contact_reply(record[field])
                except requests.RequestException:
                    record[field] = {"error": {"code": "transport_failed"}}
                record["lookup"][field] = {
                    "status": "success" if successful_step(record[field], field) else "retryable_error",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
                if interval:
                    time.sleep(interval)
            if field == "registration" and successful_step(record[field], field):
                validate_registration(subject, record[field])
            output[shop] = [item for item in output[shop] if item.get("company") != company] + [record]
            write_json(output_path, output)
        output[shop] = [item for item in output[shop] if item.get("company") != company] + [record]
        cache[company] = record
        write_json(output_path, output)
        print(f"[{index}/{len(subjects)}] enriched {shop} / {company}", flush=True)
        if index < len(subjects):
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["discover", "enrich"])
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--subjects")
    parser.add_argument("--config", default=str(Path.home() / ".codex/config.toml"))
    parser.add_argument("--interval", type=float, default=0.8)
    parser.add_argument("--allow-paid-api", action="store_true")
    args = parser.parse_args()
    if not args.allow_paid_api:
        parser.error("PAID_API_NOT_AUTHORIZED: use enterprise_pipeline.py in browser mode, or explicitly authorize --allow-paid-api")
    job_dir = ensure_job_dir(args.job_dir)
    url, auth = load_qcc_server(args.config)
    client = QccClient(url, auth)
    if args.action == "discover":
        discover(job_dir, client, args.interval)
    else:
        subjects = args.subjects or str(job_dir / "subjects.json")
        if not Path(subjects).exists():
            raise FileNotFoundError(f"Missing {subjects}; review company_candidates.json and create confirmed subject mappings")
        enrich(job_dir, client, subjects, args.interval)


if __name__ == "__main__":
    main()
