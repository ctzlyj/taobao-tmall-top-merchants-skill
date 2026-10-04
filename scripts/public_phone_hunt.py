"""Reconcile masked public phone fragments into a callable, verified number.

When one platform hides a company phone behind a member wall, the same
annual-report number is usually republished by other free public sources with
different masks (for example one hides the first four digits, another hides
the last four). This module cross-validates those fragments without touching
the restricted platform. It never turns an unverified mask into a number by
guessing; unresolved masks are preserved as-is.
"""

import argparse
import json
import re
from pathlib import Path


FULLWIDTH = {"＊": "*", "ｘ": "*", "Ｘ": "*", "×": "*"}
SEPARATORS = re.compile(r"[\s\-—–()（）]+")
VALID_FULL = re.compile(r"(?:1[3-9]\d{9}|0\d{9,10}|400\d{7})")
EMAIL_MOBILE = re.compile(r"^(1[3-9]\d{9})@")


def normalize_fragment(value):
    """Return a pattern string: digits keep positions, masks become ``*``."""
    if not isinstance(value, str):
        raise ValueError("INVALID_FRAGMENT")
    for wide, narrow in FULLWIDTH.items():
        value = value.replace(wide, narrow)
    pattern = SEPARATORS.sub("", value.strip())
    pattern = re.sub(r"[xX*]", "*", pattern)
    return pattern


def is_full(pattern):
    return "*" not in pattern and bool(VALID_FULL.fullmatch(pattern))


def valid_mask(pattern):
    return 7 <= len(pattern) <= 13 and "*" in pattern and len(re.sub(r"\*", "", pattern)) >= 3


def compatible(first, second):
    return len(first) == len(second) and all(
        a == "*" or b == "*" or a == b for a, b in zip(first, second))


def merged(patterns):
    union = list(patterns[0])
    for pattern in patterns[1:]:
        for index, char in enumerate(pattern):
            if char != "*":
                union[index] = char
    return "".join(union)


def drop_credit_code_substrings(fragments, credit_code):
    dropped = []
    kept = []
    code = re.sub(r"[^0-9a-z]", "", (credit_code or "").lower())
    for item in fragments:
        pattern = item["_pattern"]
        if "*" not in pattern and code and pattern.lower() in code:
            dropped.append({key: value for key, value in item.items() if key != "_pattern"}
                           | {"reason": "credit_code_substring"})
        else:
            kept.append(item)
    return kept, dropped


def email_phone_hints(emails):
    hints = []
    for email in emails or []:
        match = EMAIL_MOBILE.match(email.strip())
        if match:
            hints.append({"email": email.strip(), "value": match.group(1),
                          "status": "email_prefix_hint"})
    return hints


def reconcile(company, fragments, credit_code=None):
    """Cluster fragments and emit only cross-validated or labeled numbers.

    Statuses: ``cross_validated`` (multiple distinct sources determine the
    number), ``single_source`` (one source only) and unresolved masks, which
    are returned unchanged instead of being guessed into full numbers.
    """
    items = []
    for item in fragments:
        pattern = normalize_fragment(item.get("value", ""))
        if not (is_full(pattern) or valid_mask(pattern)):
            continue
        entry = {"value": item["value"], "source": item.get("source", "unknown"),
                 "source_url": item.get("source_url", ""), "label": item.get("label", ""),
                 "_pattern": pattern}
        if not entry["source_url"]:
            entry.pop("source_url")
        items.append(entry)
    items, dropped = drop_credit_code_substrings(items, credit_code)

    clusters = []
    for item in items:
        for cluster in clusters:
            if compatible(cluster["_probe"], item["_pattern"]):
                cluster["items"].append(item)
                cluster["_probe"] = merged([cluster["_probe"], item["_pattern"]])
                break
        else:
            clusters.append({"items": [item], "_probe": item["_pattern"]})

    phones = []
    unresolved = []
    for cluster in clusters:
        members = cluster["items"]
        union = merged([member["_pattern"] for member in members])
        sources = sorted({member["source"] for member in members})
        public = lambda: {
            "sources": sources,
            "labels": sorted({member["label"] for member in members if member["label"]}),
            "fragments": [{k: v for k, v in member.items() if k != "_pattern"} for member in members],
        }
        if "*" not in union:
            phones.append({"number": union,
                           "status": "cross_validated" if len(sources) > 1 else "single_source",
                           **public()})
        else:
            unresolved.append({"masked": sorted({member["value"] for member in members}), **public()})

    return {"company": company, "credit_code": credit_code or "",
            "phones": phones, "unresolved": unresolved,
            "email_hints": [], "dropped": dropped}


def reconcile_document(document):
    results = []
    for company in document.get("companies", []):
        result = reconcile(company.get("company", ""), company.get("fragments", []),
                           company.get("credit_code"))
        result["email_hints"] = email_phone_hints(company.get("emails"))
        results.append(result)
    return {"companies": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON with companies/fragments")
    parser.add_argument("--output", required=True, help="reconciled JSON output path")
    args = parser.parse_args()
    document = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = reconcile_document(document)
    output = Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reconciled {len(result['companies'])} companies -> {output}")


if __name__ == "__main__":
    main()
