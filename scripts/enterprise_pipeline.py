import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from common import write_json
from company_source_routing import company_lookup_plan
from enrich_companies import brand_query, load_subjects, normalize_contact_reply
from subject_identity import normalized_code, normalized_name, valid_qualification


SCHEMA_VERSION = 1
CACHE_MAX_AGE = 86400
FENGNIAO_FIELDS = {
    "entName": "企业名称", "uniscid": "统一社会信用代码", "dom": "注册地址",
    "personName": "法定代表人", "esDate": "成立日期", "entStatus": "登记状态",
}


class PipelineStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def now_text():
    return datetime.now(timezone.utc).isoformat()


def fresh(metadata):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(metadata["captured_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age <= CACHE_MAX_AGE


def pipeline_plan(job_dir, limit=None):
    job_dir = Path(job_dir).resolve()
    inputs = read_json(job_dir / "input_shops.json", [])
    if not inputs:
        inputs = [record for record in read_json(job_dir / "assortment_audit.json", {}).values() if record.get("passes_minimum")]
    subjects_path = job_dir / "subjects.json"
    subjects = load_subjects(subjects_path) if subjects_path.is_file() else []
    if not inputs:
        inputs = [{"shop_name": subject["shop_name"]} for subject in subjects]
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Provide normalized input_shops.json, assortment_audit.json or subjects.json")
    qualifications = read_json(job_dir / "platform_qualifications.json", {})
    targets = []
    seen = {}
    targets_by_shop = {}
    for row in inputs:
        shop = str(row.get("shop_name") or "").strip()
        if not shop:
            raise ValueError("shop_name is required; incomplete input must be resolved before lookup")
        url = row.get("shop_url") or ""
        if shop in seen:
            if url and seen[shop] and url != seen[shop]:
                raise ValueError("conflicting shop URLs in enterprise input")
            previous = targets_by_shop[shop]
            if url and not seen[shop]:
                seen[shop] = url
                previous["shop_url"] = url
            company = row.get("provided_company")
            if company and not valid_qualification(previous["qualification"]):
                if not any(normalized_name(subject.get("company")) == normalized_name(company) for subject in previous["subjects"]):
                    previous["subjects"].append({"shop_name": shop, "company": company, "selected": False})
                    previous.update(company_lookup_plan(True))
            continue
        seen[shop] = url
        qualification = qualifications.get(shop, {})
        exact_subjects = [subject for subject in subjects if subject["shop_name"] == shop]
        if valid_qualification(qualification):
            exact_subjects = [{"shop_name": shop, "company": qualification["company_name"],
                               "credit_code": qualification["credit_code"], "selected": False,
                               "evidence": qualification["source_url"], "subject_role": "平台资质待双源核验"}]
        elif not exact_subjects and (row.get("provided_company") or qualification.get("company_name")):
            exact_subjects = [{"shop_name": shop, "company": row.get("provided_company") or qualification["company_name"], "selected": False}]
        exact = bool(exact_subjects)
        target = {"shop_name": shop, "shop_url": url, "qualification": qualification,
                  "subjects": exact_subjects, "query": brand_query(shop), **company_lookup_plan(exact)}
        targets.append(target)
        targets_by_shop[shop] = target
    for target in targets:
        target["fingerprint"] = fingerprint(target)
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        targets = targets[:limit]
    return {"schema_version": SCHEMA_VERSION, "count": len(targets), "targets": targets}


def clean_response(value):
    if isinstance(value, dict):
        return {key: clean_response(item) for key, item in value.items()
                if not re.search(r"authorization|cookie|api.?key|token", str(key), re.I)}
    if isinstance(value, list):
        return [clean_response(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)Bearer\s+\S+", "[REDACTED]", value)
        return re.sub(r"(?i)([?&](?:apikey|api_key|token)=)[^&\s]+", r"\1[REDACTED]", value)
    return value


class CachedCalls:
    def __init__(self, job_dir, providers, interval):
        self.path = job_dir / "enterprise_calls.json"
        self.records = read_json(self.path, {})
        self.providers = providers
        self.interval = interval
        self.call_count = 0
        self.cache_hits = 0
        self.last_call = None

    def call(self, provider, tool, arguments):
        key = fingerprint([provider, tool, arguments])
        previous = self.records.get(key, {})
        if previous.get("status") == "success" and fresh(previous):
            try:
                cached = validate_reply(provider, tool, previous["result"])
            except PipelineStopped:
                pass
            else:
                self.cache_hits += 1
                return cached
        if self.last_call is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_call)))
        metadata = {"provider": provider, "tool": tool, "arguments": arguments, "captured_at": now_text()}
        try:
            self.call_count += 1
            self.last_call = time.monotonic()
            reply = clean_response(self.providers[provider].call(tool, arguments))
            if not isinstance(reply, dict):
                raise PipelineStopped("invalid_provider_response")
            if reply.get("error") or reply.get("isError"):
                error = reply.get("error") or {}
                code = error.get("code") if isinstance(error, dict) else None
                raise PipelineStopped("quota_or_permission" if code in (300008, 401, 403, 429) else "provider_error")
            if provider == "fengniao" and reply.get("code") not in (20000, 3000000):
                raise PipelineStopped("quota_or_permission" if reply.get("code") == 9999 else "provider_error")
            reply = validate_reply(provider, tool, reply)
        except Exception as error:
            reason = error.reason if isinstance(error, PipelineStopped) else "provider_unavailable"
            self.records[key] = {**metadata, "status": "failed", "reason": reason}
            write_json(self.path, self.records)
            raise PipelineStopped(reason) from None
        self.records[key] = {**metadata, "status": "success", "result": reply}
        write_json(self.path, self.records)
        return reply


def validate_reply(provider, tool, reply):
    if not isinstance(reply, dict) or reply.get("error") or reply.get("isError"):
        raise PipelineStopped("invalid_provider_response")
    if provider == "qcc" and tool == "get_contact_info":
        reply = normalize_contact_reply(reply)
        if not isinstance(reply.get("联系方式信息"), dict):
            raise PipelineStopped("invalid_contact_response")
    elif provider == "qcc" and tool == "get_company_registration_info":
        if not reply.get("企业名称"):
            raise PipelineStopped("registration_not_found")
    elif provider == "fengniao" and tool == "biz_fuzzy_search":
        fengniao_candidates(reply)
    elif provider == "fengniao" and tool == "biz_basic_info":
        data = reply.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("apiData"), dict):
            raise PipelineStopped("registration_not_found")
    return reply


def fengniao_candidates(reply):
    data = reply.get("data")
    if reply.get("code") == 3000000:
        return []
    if not isinstance(data, list):
        raise PipelineStopped("invalid_provider_response")
    candidates = []
    for row in data:
        if not isinstance(row, dict):
            raise PipelineStopped("invalid_provider_response")
        company = row.get("entName") or row.get("ENTNAME")
        if company and row.get("entid"):
            candidates.append({"company": company, "entid": row["entid"], "selected": False})
    return candidates


def assert_identity(registration, subject):
    company = registration.get("企业名称")
    if not company:
        raise PipelineStopped("registration_not_found")
    if subject.get("company") and normalized_name(company) != normalized_name(subject["company"]):
        raise PipelineStopped("identity_conflict")
    expected = normalized_code(subject.get("credit_code"))
    actual = normalized_code(registration.get("统一社会信用代码"))
    if expected and expected != actual:
        raise PipelineStopped("identity_conflict")


def enrich_subject(subject, calls):
    query = subject.get("credit_code") or subject["company"]
    registration = calls.call("qcc", "get_company_registration_info", {"searchKey": query})
    assert_identity(registration, subject)
    entid = subject.get("entid")
    if not entid:
        candidates = fengniao_candidates(calls.call("fengniao", "biz_fuzzy_search", {"key": subject["company"]}))
        matches = [candidate for candidate in candidates if normalized_name(candidate["company"]) == normalized_name(subject["company"])]
        if len(matches) != 1:
            raise PipelineStopped("ambiguous_or_missing_fengniao_identity")
        entid = matches[0]["entid"]
    basic_reply = calls.call("fengniao", "biz_basic_info", {"entid": entid})
    basic = (basic_reply.get("data") or {}).get("apiData")
    if not isinstance(basic, dict):
        raise PipelineStopped("invalid_provider_response")
    supplement = {target: basic.get(source, "") for source, target in FENGNIAO_FIELDS.items()}
    assert_identity(supplement, {"company": registration["企业名称"], "credit_code": registration.get("统一社会信用代码") or subject.get("credit_code")})
    contact = calls.call("qcc", "get_contact_info", {"searchKey": query, "excludeInvalidPhone": True})
    if contact.get("企业名称") and normalized_name(contact["企业名称"]) != normalized_name(registration["企业名称"]):
        raise PipelineStopped("identity_conflict")
    if not isinstance(contact.get("联系方式信息"), dict):
        raise PipelineStopped("invalid_contact_response")
    merged = dict(registration)
    field_sources = {key: "qcc" for key, value in registration.items() if value not in (None, "")}
    conflicts = {}
    for field, value in supplement.items():
        if not merged.get(field) and value:
            merged[field] = value
            field_sources[field] = "fengniao"
        elif value and merged.get(field) != value:
            conflicts[field] = {"qcc": merged[field], "fengniao": value}
    selected = bool(subject.get("selected") is True and subject.get("evidence_type") == "credit_code_match"
                    and subject.get("evidence") and normalized_code(subject.get("matched_credit_code"))
                    and normalized_code(subject.get("matched_credit_code")) == normalized_code(merged.get("统一社会信用代码")))
    return {**subject, "selected": selected, "registration": merged, "contact": contact,
            "field_sources": field_sources, "source_conflicts": conflicts, "dual_source_verified": True,
            "fengniao_registration": supplement, "captured_at": now_text(),
            "sources": [{"provider": "qcc", "url": "https://agent.qcc.com/"},
                        {"provider": "fengniao", "url": "https://www.riskbird.com/"}],
            "pending": "跨源字段冲突待复核" if conflicts else ""}


def run_pipeline(job_dir, qcc, fengniao, interval=0.8, limit=None):
    job_dir = Path(job_dir).resolve()
    plan = pipeline_plan(job_dir, limit)
    if not qcc.validate() or not fengniao.validate():
        raise RuntimeError("ENTERPRISE_SOURCES_NOT_READY: both private providers must validate")
    calls = CachedCalls(job_dir, {"qcc": qcc, "fengniao": fengniao}, interval)
    output_path = job_dir / "company_enrichment.json"
    output = read_json(output_path, {})
    candidate_path = job_dir / "company_candidates.json"
    discoveries = read_json(candidate_path, {})
    checkpoint_path = job_dir / "enterprise_checkpoint.json"
    checkpoint = {"schema_version": SCHEMA_VERSION, "plan_hash": fingerprint(plan), "status": "running", "completed": [], "pending": []}
    for index, target in enumerate(plan["targets"]):
        shop = target["shop_name"]
        try:
            subjects = target["subjects"]
            if not subjects:
                raw = calls.call("fengniao", "biz_fuzzy_search", {"key": target["query"]})
                discoveries[shop] = {"source": "fengniao", "query": target["query"], "result": raw, "captured_at": now_text(), "coverage": "provider_top5_not_exhaustive"}
                write_json(candidate_path, discoveries)
                subjects = [{**candidate, "shop_name": shop} for candidate in fengniao_candidates(raw)]
            output.setdefault(shop, [])
            for subject in subjects:
                candidate = enrich_subject(subject, calls)
                existing = [record for record in output[shop] if normalized_name(record.get("company")) != normalized_name(candidate["company"])]
                if valid_qualification(target["qualification"]):
                    existing = [{**record, "selected": False, "pending": "非本次平台执照主体，仅保留历史建联候选"} for record in existing]
                output[shop] = [*existing, candidate]
                write_json(output_path, output)
            checkpoint["completed"].append({"shop_name": shop, "fingerprint": target["fingerprint"], "candidate_count": len(subjects), "status": "completed" if subjects else "not_found", "captured_at": now_text()})
        except PipelineStopped as error:
            checkpoint.update(status="paused", reason=error.reason, current_shop=shop,
                              pending=[entry["shop_name"] for entry in plan["targets"][index:]])
            checkpoint.update(provider_calls=calls.call_count, cache_hits=calls.cache_hits)
            write_json(checkpoint_path, checkpoint)
            return checkpoint
        write_json(checkpoint_path, checkpoint)
    checkpoint.update(status="completed", provider_calls=calls.call_count, cache_hits=calls.cache_hits)
    write_json(output_path, output)
    write_json(checkpoint_path, checkpoint)
    return checkpoint


def main():
    parser = argparse.ArgumentParser(description="企业网页查询；默认只规划，付费API须显式启用")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--mode", choices=["browser", "paid-api"], default="browser")
    parser.add_argument("--allow-paid-api", action="store_true")
    parser.add_argument("--interval", type=float)
    parser.add_argument("--config", default=str(Path.home() / ".codex/config.toml"))
    args = parser.parse_args()
    if args.allow_paid_api and args.mode != "paid-api":
        parser.error("--allow-paid-api requires --mode paid-api")
    if args.mode == "paid-api" and args.execute and not args.allow_paid_api:
        parser.error("PAID_API_NOT_AUTHORIZED: explicit --allow-paid-api is required")
    if args.mode == "browser":
        from browser_enterprise import run_browser_pipeline
        interval = 20 if args.interval is None else args.interval
        if interval < 20:
            parser.error("browser --interval must be at least 20 seconds")
        result = run_browser_pipeline(args.job_dir, args.execute, args.limit, interval)
    elif not args.execute:
        result = pipeline_plan(args.job_dir, args.limit)
    else:
        from enterprise_providers import configured_providers
        qcc, fengniao = configured_providers(args.config)
        result = run_pipeline(args.job_dir, qcc, fengniao, 0.8 if args.interval is None else args.interval, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in (None, "planned", "completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
