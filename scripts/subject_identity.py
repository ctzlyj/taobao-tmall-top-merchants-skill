import re


def normalized_name(value):
    return re.sub(r"[\s（）()·•,，。]", "", str(value or "")).lower()


def normalized_code(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def valid_qualification(qualification):
    return bool(
        isinstance(qualification, dict)
        and qualification.get("status") == "verified"
        and qualification.get("evidence_type") == "platform_qualification"
        and str(qualification.get("company_name") or "").strip()
        and normalized_code(qualification.get("credit_code"))
        and str(qualification.get("source_url") or "").strip()
    )
