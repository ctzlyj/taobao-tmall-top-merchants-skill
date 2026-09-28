import json
import hashlib
import os
import random
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from common import clean_title, run_o2


HIGH_SALES_THRESHOLD = 10_000
CHECKPOINT_SCHEMA_VERSION = 2
REVIEW_RULE_VERSION = 2
EVIDENCE_MAX_AGE_SECONDS = 86400
POSITIVE_PROFILE_RESULT = "待业务复核｜类目与销量证据已核验；主图能力待人工确认；非KA待业务复核；付费/毛利仅以公开销量代理"
LOW_PROFILE_RESULT = "否｜相关SPU或高销链接未达到引入门槛；其余画像维度待复核"
PENDING_RESULT = "待核验"


class AuditPaused(RuntimeError):
    def __init__(self, reason, checkpoint_path):
        self.reason = str(reason)
        self.checkpoint_path = Path(checkpoint_path)
        super().__init__(f"{self.reason}; checkpoint={self.checkpoint_path}")


class PageAuditFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductEvidence:
    product_id: str
    product_url: str
    title: str
    sales_text: str
    sales_lower_bound: int | None
    relevant: bool
    source_type: str
    final_page_url: str
    source_urls: tuple[str, ...] = ()
    match_reason: str = ""


@dataclass(frozen=True)
class EvidenceSummary:
    relevant_spu: int
    high_sales_links: int
    products: tuple[ProductEvidence, ...]


@dataclass(frozen=True)
class ReviewDecision:
    profile_result: str
    priority: str
    relevant_spu: int
    high_sales_links: int
    complete: bool


@dataclass(frozen=True)
class ReviewTask:
    shop_name: str
    shop_url: str = ""
    category: str = ""
    source_rows: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class PageInspection:
    source_type: str
    requested_url: str
    final_url: str
    products: tuple[ProductEvidence, ...]
    failure: str = ""
    evidence_complete: bool = False


@dataclass(frozen=True)
class ShopAuditResult:
    shop_name: str
    official_shop_url: str
    evidence: tuple[ProductEvidence, ...]
    profile_result: str
    priority: str
    relevant_spu: int
    high_sales_links: int
    complete: bool
    sources: tuple[dict, ...] = ()
    category: str = ""
    rule_fingerprint: str = ""
    captured_at: str = ""


class BrowserAdapter(Protocol):
    def open_controlled_taobao(self):
        pass

    def assign_url(self, url):
        pass

    def inspect_page(self, source_type):
        pass

    def open_hot_sales(self, final_url):
        pass

    def open_public_shop_list(self, task, final_url):
        pass


class WebcliBrowserAdapter:
    def __init__(self, session="top_merchants_review", runner=run_o2):
        self.session = session
        self.runner = runner
        self.current_task = ReviewTask("")
        self.last_requested_url = ""

    def set_task(self, task):
        self.current_task = task

    def open_controlled_taobao(self):
        return self.runner(self.session, "open", "https://www.taobao.com/")

    def assign_url(self, url):
        self.last_requested_url = str(url)
        return self.runner(self.session, "eval", make_safe_assign_script(url))

    def open_hot_sales(self, final_url):
        parsed = urlparse(str(final_url or self.last_requested_url))
        if not parsed.scheme or not parsed.netloc:
            raise PageAuditFailure("shop_official_url_missing")
        target = urlunparse((parsed.scheme, parsed.netloc, "/search.htm", "", "search=y&orderType=hotsell_desc", ""))
        return self.assign_url(target)

    def open_public_shop_list(self, task, final_url):
        target = f"https://shopsearch.taobao.com/search?q={quote(task.shop_name)}"
        return self.assign_url(target)

    def inspect_page(self, source_type):
        payload = self.runner(self.session, "eval", make_inspection_script())
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        return inspection_from_payload(
            payload if isinstance(payload, dict) else {},
            source_type=source_type,
            requested_url=self.last_requested_url,
            category=self.current_task.category,
        )


def parse_review_sales_lower_bound(value):
    text = clean_title(value).replace(",", "").replace("，", "")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(亿|万)?\s*\+?", text)
    if not match:
        return None
    number = float(match.group(1))
    multiplier = {"亿": 100_000_000, "万": 10_000}.get(match.group(2), 1)
    return int(number * multiplier)


def extract_product_id(url, explicit_id=""):
    if str(explicit_id or "").strip():
        return str(explicit_id).strip()
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query)
    for key in ("id", "item_id", "itemId"):
        if query.get(key):
            return str(query[key][0]).strip()
    match = re.search(r"(?:item|i)[_/-]?(\d{5,})", parsed.path, re.I)
    return match.group(1) if match else ""


def deduplicate_products(items):
    selected = {}
    for item in items:
        identity = item.product_id or normalize_url(item.product_url)
        if not identity:
            continue
        current = selected.get(identity)
        if current is None:
            selected[identity] = item
            continue
        current_sales = current.sales_lower_bound if current.sales_lower_bound is not None else -1
        item_sales = item.sales_lower_bound if item.sales_lower_bound is not None else -1
        preferred = item if item_sales > current_sales else current
        urls = tuple(dict.fromkeys((*current.source_urls, current.final_page_url, *item.source_urls, item.final_page_url)))
        selected[identity] = replace(preferred, source_urls=tuple(url for url in urls if url))
    return list(selected.values())


def summarize_evidence(items):
    products = tuple(deduplicate_products(items))
    relevant = tuple(item for item in products if item.relevant)
    high_sales = sum(
        1 for item in relevant
        if item.sales_lower_bound is not None and item.sales_lower_bound >= HIGH_SALES_THRESHOLD
    )
    return EvidenceSummary(len(relevant), high_sales, products)


def classify_review(relevant_spu, high_sales_links, complete):
    if not complete:
        return ReviewDecision(PENDING_RESULT, PENDING_RESULT, relevant_spu, high_sales_links, False)
    if relevant_spu >= 20 and high_sales_links >= 3:
        priority = "高"
        profile = POSITIVE_PROFILE_RESULT
    elif relevant_spu >= 15 and high_sales_links >= 1:
        priority = "中"
        profile = POSITIVE_PROFILE_RESULT
    else:
        priority = "低"
        profile = LOW_PROFILE_RESULT
    return ReviewDecision(profile, priority, relevant_spu, high_sales_links, True)


def normalize_shop_name(value):
    text = clean_title(value).lower()
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"[\s\-—_·•,，。]+", "", text)


def normalize_url(value):
    parsed = urlparse(str(value or "").strip())
    if not parsed.netloc:
        return str(value or "").strip()
    return urlunparse((parsed.scheme.lower() or "https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", parsed.query, ""))


def normalize_official_shop_url(value):
    parsed = urlparse(str(value or "").strip())
    if not parsed.netloc:
        return str(value or "").strip()
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
            "",
        )
    )


def is_direct_official_shop_url(value):
    host = urlparse(str(value or "")).netloc.lower()
    return bool(
        host.endswith(".tmall.com")
        or host.endswith(".jiyoujia.com")
        or re.fullmatch(r"shop\d*\.taobao\.com", host)
    )


def stable_identity(shop_name, official_url):
    return f"{normalize_shop_name(shop_name)}|{normalize_official_shop_url(official_url)}"


def rule_fingerprint(category):
    terms = sorted(set(re.split(r"[/、,，|]+", re.sub(r"\s+", "", clean_title(category)).lower())))
    payload = [REVIEW_RULE_VERSION, terms, HIGH_SALES_THRESHOLD, 20, 3, 15, 1]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def decision_identity(shop_name, official_url, category):
    return f"{stable_identity(shop_name, official_url)}|{rule_fingerprint(category)}"


def fresh_result(data):
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["captured_at"])).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return 0 <= age <= EVIDENCE_MAX_AGE_SECONDS


def make_safe_assign_script(url):
    return f"location.assign({json.dumps(str(url), ensure_ascii=False)})"


def make_inspection_script():
    risk_terms = json.dumps(["验证码", "滑动验证", "访问被拒绝", "请按照说明进行验证"], ensure_ascii=False)
    return (
        "(()=>{const body=document.body?document.body.innerText:'';"
        "const links=[...document.querySelectorAll('a[href]')];"
        "const products=links.map(a=>{const href=a.href||'';"
        "if(!/item\\.taobao\\.com|detail\\.tmall\\.com/.test(href))return null;"
        "const box=a.closest('[data-itemid],.item,.item-card,.goods,.shop-item')||a.parentElement||a;"
        "const text=(box.innerText||a.innerText||a.title||'').trim();"
        "const sales=(text.match(/(?:人付款|已售|总销量)[：:]?\\s*[0-9.]+\\s*[万亿]?\\+?|[0-9.]+\\s*[万亿]?\\+?\\s*(?:人付款|已售)/)||[])[0]||'';"
        "return {url:href,title:(a.title||a.innerText||text).trim(),sales_text:sales};"
        "}).filter(Boolean);"
        "return {url:location.href,title:document.title,text:body.slice(0,20000),products,"
        "hidden_tmd:Boolean(document.querySelector('iframe[src*=\"_____tmd__\"],iframe[src*=\"__tmd__\"]'))," 
        "risk_terms:" + risk_terms + ".filter(t=>body.includes(t)),"
        "login_required:/亲，请登录|登录后查看/.test(body)};})()"
    )


def _category_relevant(title, category):
    normalized_category = re.sub(r"\s+", "", clean_title(category)).lower()
    normalized_title = re.sub(r"\s+", "", clean_title(title)).lower()
    if not normalized_category:
        return False
    terms = [term for term in re.split(r"[/、,，|]+", normalized_category) if term]
    return any(term in normalized_title for term in terms)


def inspection_from_payload(payload, source_type, requested_url, category=""):
    if payload.get("hidden_tmd"):
        failure = "hidden_tmd_challenge"
    elif payload.get("login_required"):
        failure = "login_required"
    elif payload.get("risk_terms"):
        failure = "risk_control"
    else:
        failure = ""
    final_url = str(payload.get("url") or requested_url or "")
    products = []
    for item in payload.get("products") or ():
        product_url = str(item.get("url") or "")
        title = clean_title(item.get("title"))
        sales_text = clean_title(item.get("sales_text"))
        product_id = extract_product_id(product_url, item.get("product_id") or "")
        if not product_id:
            continue
        products.append(
            ProductEvidence(
                product_id=product_id,
                product_url=product_url,
                title=title,
                sales_text=sales_text,
                sales_lower_bound=parse_review_sales_lower_bound(sales_text),
                relevant=_category_relevant(title, category),
                source_type=source_type,
                final_page_url=final_url,
                source_urls=(final_url,),
                match_reason=f"标题包含目标类目词：{category}" if _category_relevant(title, category) else "",
            )
        )
    return PageInspection(
        source_type=source_type,
        requested_url=requested_url,
        final_url=final_url,
        products=tuple(products),
        failure=failure,
        evidence_complete=False,
    )


def _source_record(inspection):
    return {
        "source_type": inspection.source_type,
        "requested_url": inspection.requested_url,
        "final_url": inspection.final_url,
    }


def _inspection_products(inspection):
    return [
        replace(
            item,
            source_type=inspection.source_type,
            final_page_url=inspection.final_url,
            source_urls=tuple(dict.fromkeys((*item.source_urls, inspection.final_url))),
        )
        for item in inspection.products
    ]


def audit_shop(task, browser):
    if hasattr(browser, "set_task"):
        browser.set_task(task)
    browser.open_controlled_taobao()
    browser.assign_url(task.shop_url)
    inspections = []

    home = browser.inspect_page("shop_home")
    inspections.append(home)
    if home.failure:
        raise PageAuditFailure(home.failure)
    evidence = _inspection_products(home)
    final_url = home.final_url
    official_shop_url = home.final_url

    def sufficient():
        summary = summarize_evidence(evidence)
        relevant = [item for item in summary.products if item.relevant]
        high = summary.relevant_spu >= 20 and summary.high_sales_links >= 3
        exhaustive = any(item.evidence_complete for item in inspections)
        sales_known = all(item.sales_lower_bound is not None for item in relevant)
        return bool(relevant) and (high or (exhaustive and sales_known))

    if not sufficient():
        browser.open_hot_sales(final_url)
        hot_sales = browser.inspect_page("shop_hot_sales")
        inspections.append(hot_sales)
        if hot_sales.failure:
            raise PageAuditFailure(hot_sales.failure)
        evidence.extend(_inspection_products(hot_sales))
        final_url = hot_sales.final_url or final_url
        official_shop_url = official_shop_url or hot_sales.final_url

    if not sufficient():
        browser.open_public_shop_list(task, final_url)
        public_list = browser.inspect_page("public_shop_list")
        inspections.append(public_list)
        if public_list.failure:
            raise PageAuditFailure(public_list.failure)
        evidence.extend(_inspection_products(public_list))
        final_url = public_list.final_url or final_url

    summary = summarize_evidence(evidence)
    evidence_complete = sufficient()
    result = classify_review(summary.relevant_spu, summary.high_sales_links, complete=evidence_complete)
    return ShopAuditResult(
        shop_name=task.shop_name,
        official_shop_url=official_shop_url,
        evidence=summary.products,
        profile_result=result.profile_result,
        priority=result.priority,
        relevant_spu=result.relevant_spu,
        high_sales_links=result.high_sales_links,
        complete=result.complete,
        sources=tuple(_source_record(item) for item in inspections),
        category=task.category,
        rule_fingerprint=rule_fingerprint(task.category),
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def _product_from_dict(data):
    return ProductEvidence(
        product_id=str(data.get("product_id") or ""),
        product_url=str(data.get("product_url") or ""),
        title=str(data.get("title") or ""),
        sales_text=str(data.get("sales_text") or ""),
        sales_lower_bound=data.get("sales_lower_bound"),
        relevant=bool(data.get("relevant")),
        source_type=str(data.get("source_type") or ""),
        final_page_url=str(data.get("final_page_url") or ""),
        source_urls=tuple(data.get("source_urls") or ()),
        match_reason=str(data.get("match_reason") or ""),
    )


def _result_from_dict(data):
    return ShopAuditResult(
        shop_name=str(data.get("shop_name") or ""),
        official_shop_url=str(data.get("official_shop_url") or ""),
        evidence=tuple(_product_from_dict(item) for item in data.get("evidence") or ()),
        profile_result=str(data.get("profile_result") or PENDING_RESULT),
        priority=str(data.get("priority") or PENDING_RESULT),
        relevant_spu=int(data.get("relevant_spu") or 0),
        high_sales_links=int(data.get("high_sales_links") or 0),
        complete=bool(data.get("complete")),
        sources=tuple(data.get("sources") or ()),
        category=str(data.get("category") or ""),
        rule_fingerprint=str(data.get("rule_fingerprint") or ""),
        captured_at=str(data.get("captured_at") or ""),
    )


def _result_to_dict(result):
    return asdict(result)


def write_checkpoint_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_checkpoint(path):
    path = Path(path)
    if not path.is_file():
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "status": "new",
            "completed": {},
            "aliases": {},
            "pending": [],
            "current_task": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid review checkpoint: {error}") from error
    if payload.get("schema_version") == 1:
        backup = path.with_name(path.name + ".v1.bak")
        if not backup.exists():
            with backup.open("xb") as handle:
                handle.write(path.read_bytes())
        payload = {"schema_version": CHECKPOINT_SCHEMA_VERSION, "status": "migrated",
                   "completed": {}, "aliases": {}, "legacy_checkpoint": str(backup)}
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ValueError("unsupported review checkpoint schema")
    payload.setdefault("completed", {})
    payload.setdefault("aliases", {})
    return payload


def _find_reusable(task, checkpoint):
    completed = checkpoint.get("completed") or {}
    aliases = checkpoint.get("aliases") or {}
    candidate_keys = []
    if task.shop_url:
        if is_direct_official_shop_url(task.shop_url):
            candidate_keys.append(decision_identity(task.shop_name, task.shop_url, task.category))
        alias = aliases.get(normalize_url(task.shop_url))
        if alias:
            candidate_keys.append(alias)
    matches = [completed[key] for key in dict.fromkeys(candidate_keys) if key in completed
               and normalize_shop_name(completed[key].get("shop_name")) == normalize_shop_name(task.shop_name)
               and completed[key].get("rule_fingerprint") == rule_fingerprint(task.category)
               and fresh_result(completed[key])]
    if len({item.get("official_shop_url") for item in matches}) > 1:
        raise PageAuditFailure("shop_identity_conflict")
    return _result_from_dict(matches[0]) if matches else None


def audit_queue(tasks, browser, checkpoint_path, sleeper=time.sleep, rng=random.uniform):
    checkpoint_path = Path(checkpoint_path)
    checkpoint = load_checkpoint(checkpoint_path)
    results = []
    visited_count = 0
    task_list = list(tasks)
    checkpoint["pending"] = [asdict(task) for task in task_list]
    for index, task in enumerate(task_list):
        reusable = _find_reusable(task, checkpoint)
        if reusable and reusable.complete:
            results.append(reusable)
            continue
        if visited_count:
            sleeper(rng(18, 22))
        try:
            visited_count += 1
            result = audit_shop(task, browser)
            normalized_name = normalize_shop_name(task.shop_name)
            existing_urls = {
                normalize_official_shop_url(item.get("official_shop_url"))
                for key, item in checkpoint["completed"].items()
                if key.split("|", 1)[0] == normalized_name and item.get("official_shop_url")
            }
            if existing_urls and normalize_official_shop_url(result.official_shop_url) not in existing_urls:
                raise PageAuditFailure("shop_identity_conflict")
        except (PageAuditFailure, RuntimeError) as error:
            checkpoint["status"] = "paused"
            checkpoint["failure"] = str(error)
            checkpoint["current_task"] = asdict(task)
            checkpoint["pending"] = [asdict(item) for item in task_list[index:]]
            write_checkpoint_atomic(checkpoint_path, checkpoint)
            raise AuditPaused(str(error), checkpoint_path) from error
        identity = decision_identity(task.shop_name, result.official_shop_url, task.category)
        checkpoint["completed"][identity] = _result_to_dict(result)
        if task.shop_url:
            checkpoint["aliases"][normalize_url(task.shop_url)] = identity
        checkpoint["status"] = "running"
        checkpoint["current_task"] = None
        checkpoint["pending"] = [asdict(item) for item in task_list[index + 1:]]
        write_checkpoint_atomic(checkpoint_path, checkpoint)
        results.append(result)
    checkpoint["status"] = "completed"
    checkpoint["current_task"] = None
    checkpoint["pending"] = []
    write_checkpoint_atomic(checkpoint_path, checkpoint)
    return results
