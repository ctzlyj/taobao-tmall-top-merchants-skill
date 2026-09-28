import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from browser_contacts import PHONE, compact, contact_rows, public_url, recent, text
from common import write_json

STOP_CODES = {
    "AUTH_REQUIRED", "CHALLENGE_REQUIRED", "RATE_LIMITED", "CONTACT_ACCESS_REQUIRED",
    "ACCESS_BLOCKED", "IDENTITY_CONFLICT", "INCOMPLETE_IDENTITY", "UNRECOGNIZED_CONTACT",
    "INVALID_RESPONSE", "PAGE_NOT_READY", "WEBCLI_UNAVAILABLE", "PAID_SOURCE_REJECTED",
}


class BrowserStopped(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if reason in STOP_CODES else "INVALID_RESPONSE"
        super().__init__(self.reason)


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def normalize_target(row):
    if not isinstance(row, dict):
        raise ValueError("INVALID_BROWSER_TARGET")
    target = {key: text(row.get(key, "")) for key in
              ("shop_name", "company", "credit_code", "registration_number", "legal_name", "address")}
    target["source_url"] = public_url(row["source_url"], riskbird=True) if row.get("source_url") else ""
    target["credit_code"] = compact(target["credit_code"]).upper()
    target["registration_number"] = compact(target["registration_number"])
    if not target["shop_name"]:
        target["shop_name"] = target["company"]
    if not target["shop_name"]:
        raise ValueError("SHOP_OR_COMPANY_REQUIRED")
    for field, pattern in (("credit_code", r"[0-9A-Z]{18}"), ("registration_number", r"\d{15}")):
        if target[field] and not re.fullmatch(pattern, target[field]):
            raise ValueError("INVALID_IDENTITY_FIELD")
    target["contact_evidence"] = row.get("contact_evidence", [])
    contact_rows({"phones": [], "company": target["company"]}, target["contact_evidence"])
    target["ready"] = bool(target["company"] and target["source_url"]
                           and (target["credit_code"] or target["registration_number"]))
    return target


def targets_for(job_dir, limit):
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    path = job_dir / "browser_targets.json"
    if path.is_file():
        rows = load_json(path, [])
    else:
        from enterprise_pipeline import pipeline_plan
        plan = pipeline_plan(job_dir)
        rows = [{"shop_name": target["shop_name"], "company": subject.get("company", ""),
                 "credit_code": subject.get("credit_code", ""),
                 "registration_number": subject.get("registration_number", "")}
                for target in plan["targets"] for subject in (target["subjects"] or [{}])]
    if not isinstance(rows, list) or not rows:
        raise ValueError("BROWSER_TARGETS_REQUIRED")
    return [normalize_target(row) for row in rows[:limit]], len(rows)


def normalize_record(raw, target):
    if not isinstance(raw, dict) or raw.get("identity_verified") is not True:
        raise BrowserStopped("INVALID_RESPONSE")
    if raw.get("source") != "riskbird_web" or raw.get("paid_api_calls") != 0:
        raise BrowserStopped("PAID_SOURCE_REJECTED")
    result = {key: text(raw.get(key, "")) for key in
              ("company", "credit_code", "registration_number", "legal_name", "address", "captured_at")}
    for field in ("company", "credit_code", "registration_number", "legal_name"):
        if target[field] and compact(result[field]) != compact(target[field]):
            raise BrowserStopped("IDENTITY_CONFLICT")
    if not re.fullmatch(r"[0-9A-Z]{18}", result["credit_code"]) or not recent(result["captured_at"]):
        raise BrowserStopped("INCOMPLETE_IDENTITY")
    result["source_url"] = public_url(raw.get("source_url", ""), riskbird=True)
    phones, emails = raw.get("phones"), raw.get("emails")
    if not isinstance(phones, list) or not isinstance(emails, list):
        raise BrowserStopped("UNRECOGNIZED_CONTACT")
    if any(not isinstance(phone, str) or not PHONE.fullmatch(phone) for phone in phones):
        raise BrowserStopped("UNRECOGNIZED_CONTACT")
    if any(not isinstance(email, str) or not re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", email) for email in emails):
        raise BrowserStopped("UNRECOGNIZED_CONTACT")
    status = raw.get("contact_status")
    if status not in {"visible_records", "visible_records_partial", "not_disclosed", "masked"}:
        raise BrowserStopped("UNRECOGNIZED_CONTACT")
    if bool(phones or emails) != (status in {"visible_records", "visible_records_partial"}):
        raise BrowserStopped("UNRECOGNIZED_CONTACT")
    result.update(phones=list(dict.fromkeys(phones)), emails=list(dict.fromkeys(emails)),
                  contact_status=status, shop_name=target["shop_name"], identity_verified=True,
                  source="riskbird_web", paid_api_calls=0, dual_source_verified=False,
                  shop_subject_verified=False, contact_scope="company_header_visible_fields_only")
    result["field_conflicts"] = ({"address": {"provided": target["address"], "web": result["address"]}}
                                 if target["address"] and target["address"] != result["address"] else {})
    result["contacts"] = contact_rows(result, target["contact_evidence"])
    return result


def webcli_command():
    launcher = shutil.which("webcli")
    if not launcher:
        raise BrowserStopped("WEBCLI_UNAVAILABLE")
    if os.name != "nt":
        return [launcher]
    package = Path(launcher).parent / "node_modules" / "@jd" / "webcli"
    manifest = load_json(package / "package.json", {})
    node = shutil.which("node")
    entry_name = manifest.get("bin", {}).get("webcli", "")
    entry = (package / entry_name).resolve()
    if manifest.get("name") != "@jd/webcli" or not node or not entry.is_file() or not entry.is_relative_to(package.resolve()):
        raise BrowserStopped("WEBCLI_UNAVAILABLE")
    return [node, str(entry)]


class RiskbirdWebProvider:
    def read(self, target):
        arguments = ["riskbird", "company", target["source_url"], "--company", target["company"]]
        for field in ("credit_code", "registration_number", "legal_name"):
            if target[field]:
                arguments.extend(["--" + field.replace("_", "-"), target[field]])
        environment = os.environ.copy()
        environment["WEBCLI_WINDOW"] = "background"
        environment.pop("NODE_OPTIONS", None)
        reply = subprocess.run(webcli_command() + arguments + ["-f", "json"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=120, check=False, env=environment,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if reply.returncode:
            message = reply.stdout + reply.stderr
            reason = next((code for code in sorted(STOP_CODES) if code in message), "INVALID_RESPONSE")
            raise BrowserStopped(reason)
        try:
            payload = json.loads(reply.stdout)
            if isinstance(payload, dict) and (payload.get("ok") is False or payload.get("success") is False or payload.get("error")):
                raise ValueError()
            if isinstance(payload, dict) and "data" in payload:
                payload = payload["data"]
            if isinstance(payload, list) and len(payload) == 1:
                payload = payload[0]
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (ValueError, TypeError):
            raise BrowserStopped("INVALID_RESPONSE") from None


def run_browser_pipeline(job_dir, execute=False, limit=None, interval=20, provider=None):
    if not math.isfinite(interval) or interval < 0:
        raise ValueError("INVALID_INTERVAL")
    job_dir = Path(job_dir).resolve()
    targets, total_count = targets_for(job_dir, limit)
    result = {"schema_version": 1, "mode": "browser", "status": "planned", "paid_api_calls": 0,
              "count": len(targets), "total_count": total_count, "scope_complete": False,
              "browser_calls": 0, "cache_hits": 0, "results": [], "pending": [], "failures": []}
    if not execute:
        result["targets"] = [{key: value for key, value in target.items() if key != "contact_evidence"} for target in targets]
        return result
    checkpoint_path = job_dir / "browser_checkpoint.json"
    previous = load_json(checkpoint_path, {})
    if previous and (previous.get("schema_version") != 1 or previous.get("mode") != "browser"):
        raise ValueError("CHECKPOINT_SCHEMA_MISMATCH")
    cache = {item.get("fingerprint"): item for item in previous.get("results", [])}
    cache_path = job_dir / "browser_cache.json"
    saved_cache = load_json(cache_path, {"schema_version": 1, "records": {}})
    if saved_cache.get("schema_version") != 1 or not isinstance(saved_cache.get("records"), dict):
        raise ValueError("CACHE_SCHEMA_MISMATCH")
    cache.update(saved_cache["records"])
    last_call = None
    for index, target in enumerate(targets):
        digest = fingerprint(target)
        if not target["ready"]:
            result["pending"].append({"shop_name": target["shop_name"], "reason": "NEEDS_BROWSER_DISCOVERY"})
            continue
        try:
            cached = cache.get(digest)
            if cached and recent(cached.get("captured_at")):
                record = normalize_record(cached, target)
                result["cache_hits"] += 1
            else:
                if last_call is not None:
                    time.sleep(max(0, interval - (time.monotonic() - last_call)))
                provider = provider or RiskbirdWebProvider()
                last_call = time.monotonic()
                result["browser_calls"] += 1
                record = normalize_record(provider.read(target), target)
            record = {**record, "fingerprint": digest}
            result["results"].append(record)
            cache[digest] = record
            write_json(cache_path, {"schema_version": 1, "records": cache})
        except BrowserStopped as error:
            result.update(status="paused", reason=error.reason)
            result["pending"].extend({"shop_name": pending["shop_name"], "reason": error.reason} for pending in targets[index:])
            break
        except (TimeoutError, subprocess.TimeoutExpired, OSError):
            result["failures"].append({"shop_name": target["shop_name"], "reason": "BROWSER_TRANSPORT_FAILED"})
        except (ValueError, TypeError, KeyError):
            result.update(status="paused", reason="INVALID_RESPONSE")
            result["pending"].extend({"shop_name": pending["shop_name"], "reason": "INVALID_RESPONSE"} for pending in targets[index:])
            break
        result["status"] = "running"
        write_json(checkpoint_path, result)
    if result["status"] != "paused":
        result["status"] = "needs_browser_discovery" if result["pending"] else "partial" if result["failures"] else "completed"
    result["scope_complete"] = result["status"] == "completed" and len(targets) == total_count
    write_json(checkpoint_path, result)
    saved = load_json(checkpoint_path, {})
    if saved != result:
        raise RuntimeError("CHECKPOINT_READBACK_FAILED")
    return saved
