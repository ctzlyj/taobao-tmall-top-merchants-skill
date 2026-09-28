import argparse
import json
import re
import time
from datetime import datetime

from common import BrowserTransientError, classify_item, clean_title, ensure_job_dir, load_job, make_search_script, normalize, open_search, parse_sales_lower_bound, platform_name, run_o2, write_json


def candidate_payment_lower_bound(row):
    return sum(parse_sales_lower_bound(item.get("sales")) or 0 for item in row.get("items", []))


def select_candidates(job, rows, limit=None):
    selected = []
    hint_patterns = [pattern for pattern in job.get("merchant_hint_patterns", []) if pattern]
    hints = re.compile("|".join(f"(?:{pattern})" for pattern in hint_patterns), re.I) if hint_patterns else None
    excluded_patterns = [pattern for pattern in job.get("excluded_shop_patterns", []) if pattern]
    excluded = re.compile("|".join(f"(?:{pattern})" for pattern in excluded_patterns), re.I) if excluded_patterns else None
    minimum_payment = job.get("minimum_payment_lower_bound", 0)
    minimum_quality_coverage = job.get("minimum_quality_query_coverage", 1)
    sales_top_n_mode = job.get("sales_top_n_mode", False)
    for row in rows:
        discovery = row.get("discovered_target_spu", 0)
        coverage = len(row.get("queries", []))
        primary = discovery >= job.get("minimum_discovery_spu", 3)
        broad_recall = discovery >= job.get("secondary_discovery_spu", 1) and coverage >= job.get("secondary_query_coverage", 4)
        merchant_hint = discovery >= job.get("secondary_discovery_spu", 1) and bool(hints and hints.search(row.get("shop_name", "")))
        sales_top = sales_top_n_mode and discovery >= job.get("secondary_discovery_spu", 1)
        if not (primary or broad_recall or merchant_hint or sales_top):
            continue
        audit_reason = "sales_top" if sales_top_n_mode else "primary" if primary else "query_coverage" if broad_recall else "merchant_hint"
        payment_lower_bound = candidate_payment_lower_bound(row)
        if payment_lower_bound < minimum_payment:
            continue
        if coverage < minimum_quality_coverage and audit_reason not in {"merchant_hint", "sales_top"}:
            continue
        if excluded and excluded.search(row.get("shop_name", "")):
            continue
        selected.append({**row, "audit_reason": audit_reason, "payment_lower_bound": payment_lower_bound, "query_coverage": coverage})
    selected.sort(key=lambda row: (-row["payment_lower_bound"], -row.get("discovered_target_spu", 0), -row["query_coverage"], row["shop_name"]))
    return selected[: limit or job.get("max_candidate_shops", 30)]


def audit_rows(job, target, rows):
    items = {}
    platform_counts = {"淘宝": 0, "天猫": 0}
    for item in rows:
        if normalize(item.get("shop")) != normalize(target["shop_name"]):
            continue
        platform_counts[platform_name(item)] += 1
        item_id = item.get("item_id") or f'{clean_title(item.get("title"))}::{target["shop_name"]}'
        items.setdefault(item_id, item)
    relevant, electric, accessories, unrelated = [], [], [], []
    for item in items.values():
        classification = classify_item(item.get("title"), job)
        output = {**item, "title": clean_title(item.get("title"))}
        if classification["relevant"]:
            relevant.append(output)
        elif classification["accessory"] and classification["category_match"]:
            accessories.append(output)
        elif classification["electric"] and classification["category_match"]:
            electric.append(output)
        else:
            unrelated.append(output)
    total = len(items)
    share = len(relevant) / total if total else 0
    platform = max(platform_counts, key=platform_counts.get) if total else target["platform"]
    passes = len(relevant) >= job["min_spu"] and share >= job["min_share"]
    grade = "高匹配" if passes and share >= job["high_match_share"] else "达标" if passes else "不达标"
    return {
        "category": job["category"],
        "shop_name": target["shop_name"],
        "platform": platform,
        "expected_platform": target["platform"],
        "exact_shop_spu_seen": total,
        "target_spu": len(relevant),
        "electric_spu": len(electric),
        "accessory_spu": len(accessories),
        "unrelated_spu": len(unrelated),
        "target_share": round(share, 4),
        "measurement_scope": "shop_name_search_sample",
        "inventory_complete": False,
        "sample_target_share": round(share, 4),
        "full_store_target_share": None,
        "passes_minimum": passes,
        "match_grade": grade,
        "shop_url": next((item.get("shop_url") for item in items.values() if item.get("shop_url")), target.get("shop_url", "")),
        "user_id": next((item.get("user_id") for item in items.values() if item.get("user_id")), target.get("user_id", "")),
        "target_items": relevant,
        "electric_samples": electric[:8],
        "accessory_samples": accessories[:8],
        "unrelated_samples": unrelated[:8],
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def classify_browser_failure(message):
    text = str(message or "")
    if "TAOBAO_RISK_CONTROL" in text:
        return "risk_control"
    if any(term in text.lower() for term in ["timed out", "timeout", "operation was aborted", "bridge disconnected", "connection reset"]):
        return "browser_transient_error"
    return "browser_error"


def should_reuse_recorded_failure(errors, shop_name):
    return errors.get(shop_name, {}).get("status") == "risk_control"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--session", default="top_merchants_audit")
    parser.add_argument("--pages", type=int)
    parser.add_argument("--interval", type=int)
    parser.add_argument("--max-shops", type=int)
    parser.add_argument("--skip-shop", action="append", default=[])
    args = parser.parse_args()
    job_dir = ensure_job_dir(args.job_dir)
    job = load_job(job_dir)
    candidate_data = json.loads((job_dir / "candidates.json").read_text(encoding="utf-8"))
    candidates = select_candidates(job, candidate_data["candidates"], args.max_shops)
    output_path = job_dir / "assortment_audit.json"
    error_path = job_dir / "audit_errors.json"
    errors = json.loads(error_path.read_text(encoding="utf-8")) if error_path.exists() else {}
    existing = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
    pages = args.pages or job.get("pages", 2)
    interval = args.interval or job.get("interval_seconds", 20)
    for index, target in enumerate(candidates, 1):
        shop_name = target["shop_name"]
        if shop_name in args.skip_shop:
            errors[shop_name] = {"status": "skipped_by_operator", "reason": "explicit --skip-shop after repeated transient browser failure", "target": target}
            write_json(error_path, errors)
            print(f"[{index}/{len(candidates)}] skipped {shop_name}", flush=True)
            continue
        if shop_name in existing:
            print(f"[{index}/{len(candidates)}] cached {shop_name}", flush=True)
            continue
        if should_reuse_recorded_failure(errors, shop_name):
            print(f"[{index}/{len(candidates)}] cached risk_control {shop_name}", flush=True)
            continue
        print(f"[{index}/{len(candidates)}] {target['platform']} / {shop_name}", flush=True)
        try:
            open_search(args.session, shop_name)
            time.sleep(7)
            rows = run_o2(args.session, "eval", make_search_script(shop_name, pages))
        except BrowserTransientError as error:
            message = str(error)
            errors[shop_name] = {"status": classify_browser_failure(message), "reason": message, "target": target}
            write_json(error_path, errors)
            print(f"  browser_error={message}; recorded and continuing", flush=True)
            continue
        except RuntimeError as error:
            message = str(error)
            status = classify_browser_failure(message)
            errors[shop_name] = {"status": status, "reason": message, "target": target}
            write_json(error_path, errors)
            if status == "risk_control":
                raise
            print(f"  browser_error={message}; recorded and continuing", flush=True)
            continue
        existing[shop_name] = audit_rows(job, target, rows)
        errors.pop(shop_name, None)
        write_json(output_path, existing)
        write_json(error_path, errors)
        record = existing[shop_name]
        print(f"  target={record['target_spu']}/{record['exact_shop_spu_seen']} share={record['target_share']:.1%} {record['match_grade']}", flush=True)
        if index < len(candidates):
            time.sleep(interval)
    print(f"passed={sum(row['passes_minimum'] for row in existing.values())} audited={len(existing)}")


if __name__ == "__main__":
    main()
