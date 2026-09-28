import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

PHONE = re.compile(r"(?:1[3-9]\d{9}|0\d{2,3}[-\s]?\d{7,8}|400[-\s]?\d{3}[-\s]?\d{4})")
SECRET = re.compile(r"(?:cookie|authorization|api[-_]?key|token|password)\s*[:=]", re.I)


def text(value):
    if not isinstance(value, str) or SECRET.search(value):
        raise ValueError("INVALID_PUBLIC_FIELD")
    return value.strip()


def compact(value):
    return re.sub(r"\s+", "", text(value))


def public_url(value, riskbird=False):
    try:
        value = text(value)
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.fragment and not parsed.port)
        parameters = parse_qsl(parsed.query, keep_blank_values=True)
        valid = valid and not any(re.search(r"cookie|token|auth|key|password|session", key, re.I)
                                  for key, unused in parameters)
        if riskbird:
            valid = (valid and parsed.hostname == "www.riskbird.com"
                     and re.fullmatch(r"/ent/[^/]+\.html", parsed.path)
                     and all(key == "entid" for key, unused in parameters))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("INVALID_SOURCE_URL")
    return value


def recent(value):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds()
        return 0 <= age <= 86400
    except (TypeError, ValueError):
        return False


def phone_key(value):
    return re.sub(r"[-\s]", "", text(value))


def contact_rows(record, evidence):
    if not isinstance(evidence, list):
        raise ValueError("INVALID_CONTACT_EVIDENCE")
    rows = []
    for phone in record["phones"]:
        matches = []
        for item in evidence:
            if not isinstance(item, dict) or phone_key(item.get("phone", "")) != phone_key(phone):
                continue
            if item.get("association") != "explicit" or not recent(item.get("captured_at")):
                continue
            person = text(item.get("person", ""))
            role = text(item.get("role", ""))
            excerpt = text(item.get("excerpt", ""))
            if re.search(r"法定代表人|法人|股东", excerpt) and not re.search(r"联系人|商务|业务|招商|销售", excerpt):
                continue
            if not person or person not in excerpt or (role and role not in excerpt):
                continue
            if phone_key(phone) not in [phone_key(match) for match in PHONE.findall(excerpt)]:
                continue
            matches.append({"phone": phone, "person": person, "role": role,
                            "company": text(item.get("company", "")),
                            "source_url": public_url(item.get("source_url", "")),
                            "captured_at": text(item["captured_at"]), "excerpt": excerpt})
        result = {
            "phone": phone, "person": "未披露/未确认", "role": "未披露/未确认",
            "attribution": "企业页面公开电话，未确认个人使用人", "confirmation": "未确认",
            "opening": f"您好，请问是{record['company']}的商务联系电话吗？请问怎么称呼您、是否负责这项合作？",
            "evidence": matches,
        }
        conflicts = (len({item["person"] for item in matches}) > 1
                     or any(compact(item["company"]) != compact(record["company"]) for item in matches))
        roles = {item["role"] for item in matches if item["role"]}
        if conflicts or len(roles) > 1:
            result.update(confirmation="来源冲突，暂停认定", opening="号码归属存在冲突，先核实企业与联系人，不按任一姓名称呼。")
        elif matches:
            person = matches[0]["person"]
            result.update(person=person, role=next(iter(roles), "未披露/未确认"),
                          attribution="公开经营页面明确对应号码与联系人；见逐号码证据",
                          confirmation="来源明确标注（未拨通）",
                          opening=f"您好，请问是页面上标注的联系人{person}吗？想确认您是否负责这项合作。")
        rows.append(result)
    return rows
