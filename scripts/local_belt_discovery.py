"""Region/industrial-belt merchant discovery for offline visit outreach.

Zero-paid-API workflow over already-logged-in background browser sessions:
1. collect: region+keyword searches on the QCC web search page.
2. parse:   dedupe company cards into a ranked companies.json.
3. verify:  Riskbird exact-name search, entid-only detail URL, adapter identity
            verification and public header contacts; IDENTITY_CONFLICT rows are
            preserved instead of being force-passed.

The script never uses paid APIs, never takes over working tabs, and stops the
current query batch when a risk-control challenge is detected.
"""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from common import write_json


QCC_SEARCH_URL = "https://www.qcc.com/web/search?key={keyword}"
RISKBIRD_SEARCH_URL = "https://www.riskbird.com/search/company?keyword={keyword}"

NAME = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()]{3,28}?(?:有限公司|有限责任公司|股份有限公司|"
                  r"合伙企业|个体工商户|百货店|制品厂|日用品厂|木器厂|工艺品厂|木业|塑业|商贸|商行|"
                  r"门市部|服务中心|工作室|经营部))")
STATUS = re.compile(r"\b(存续|注销|吊销|迁出|在业|开业)\b")
LEGAL = re.compile(r"(?:法定代表人|经营者)[:：]?\s*([\u4e00-\u9fa5·]{2,5})")
CAPITAL = re.compile(r"(?:注册资本|资金数额)[:：]?\s*([0-9.,万亿]+(?:万)?(?:元|人民币)?)")
DATE = re.compile(r"成立日期[:：]?\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
CREDIT = re.compile(r"([0-9A-Z]{18})")
PHONE = re.compile(r"电话[:：]?\s*([0-9\-*]{7,14})")
EMAIL = re.compile(r"邮箱[:：]?\s*([A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+)")
ADDRESS = re.compile(r"地址[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9（）()、，,；;·—\-]{6,60}?(?:复制|$))")
SCOPE = re.compile(r"经营范围[:：]?\s*([\u4e00-\u9fa5A-Za-z0-9:：、，,；;（）()·—\-]{4,180}?(?:复制|$))")

PRODUCT_WORDS = (
    "沐浴", "洗漱", "泡脚", "泡澡", "按摩", "刮痧", "搓澡", "浴帽", "浴刷", "皂盒", "分装", "喷雾",
    "汗蒸", "痒痒", "木梳", "梳", "日用品", "家居", "清洁", "足浴", "洗浴", "坐浴", "起泡", "海绵",
    "塑料制品", "木制品", "纺织品", "电子商务",
)
MANUFACTURE_WORDS = ("生产", "制造", "加工", "厂")

QCC_EXTRACT = """
(() => {
  const text = document.body ? document.body.innerText : "";
  const totalMatch = text.match(/为您找到\\s*([0-9]+)\\s*条相关结果/);
  const cards = Array.from(document.querySelectorAll('section,div'))
    .filter(e => e.innerText && e.innerText.includes('统一社会信用代码') && e.innerText.length > 50 && e.innerText.length < 1500)
    .slice(0, 12)
    .map(e => e.innerText.trim().slice(0, 800));
  const challenge = /安全验证|请完成下方验证|访问过于频繁/.test(text.slice(0, 3000));
  return {url: location.href.slice(0, 220), total: totalMatch ? totalMatch[1] : null,
          challenge, cards: [...new Set(cards)], bodyLength: text.length};
})()
"""

RISKBIRD_SEARCH_EXTRACT = """
(() => {
  const text = document.body ? document.body.innerText : "";
  const blocked = /请先登录|安全验证|访问过于频繁|请求频率过快/.test(text);
  const cards = [...document.querySelectorAll('.company-container')]
    .map(element => ({
      text: element.innerText.slice(0, 300),
      links: [...element.querySelectorAll('a')]
        .filter(link => link.href.includes('/ent/'))
        .map(link => ({name: link.innerText, url: link.href}))
    })).slice(0, 6);
  return {blocked, cards, bodyLength: text.length, url: location.href};
})()
"""

RISKBIRD_IP_CLICK_AND_READ = """
(() => {
  const text = document.body ? document.body.innerText : "";
  const countOf = (label) => {
    const match = text.match(new RegExp(label + '\\\\s*\\\\n?\\\\s*(\\\\d+)'));
    return match ? Number(match[1]) : null;
  };
  const tabs = Array.from(document.querySelectorAll('.info-tabs-item'));
  const target = tabs.find(element => element.innerText.trim().startsWith('知识产权'));
  let clicked = false;
  if (target) {
    target.scrollIntoView({block: 'center'});
    target.click();
    clicked = true;
  }
  const tmStart = text.lastIndexOf('商标信息');
  return {url: location.href, ipClicked: clicked,
          trademarkCount: countOf('商标信息'), patentCount: countOf('专利信息'),
          copyrightCount: countOf('作品著作权'),
          rawTmText: tmStart >= 0 ? text.slice(tmStart, tmStart + 2600) : ""};
})()
"""

TRADEMARK_ROW = re.compile(
    r"(\d+)\t([^\t]+)\t(第\d+类[^\t]*)\t([^\t]*)\t(\d+)\t(\d{4}-\d{2}-\d{2})")

RELEVANT_TRADEMARK_CLASSES = (
    "第21类", "第20类", "第35类", "第10类", "第28类", "第08类", "第03类", "第11类",
)


def build_queries(region, keywords, suffixes=()):
    if not isinstance(region, str) or not region.strip():
        raise ValueError("REGION_REQUIRED")
    queries = []
    for keyword in keywords or []:
        keyword = str(keyword).strip()
        if not keyword:
            continue
        queries.append(f"{region.strip()} {keyword}")
        for suffix in suffixes or ():
            queries.append(f"{region.strip()} {keyword} {str(suffix).strip()}")
    if not queries:
        raise ValueError("KEYWORDS_REQUIRED")
    return list(dict.fromkeys(queries))


def parse_card(card):
    name_match = NAME.search(card.replace("\n", " "))
    return {
        "raw": card,
        "name": name_match.group(1) if name_match else None,
        "status": (STATUS.search(card).group(1) if STATUS.search(card) else None),
        "legal": (LEGAL.search(card).group(1) if LEGAL.search(card) else None),
        "capital": (CAPITAL.search(card).group(1) if CAPITAL.search(card) else None),
        "date": (DATE.search(card).group(1) if DATE.search(card) else None),
        "credit": (CREDIT.search(card).group(1) if CREDIT.search(card) else None),
        "phone": (PHONE.search(card).group(1) if PHONE.search(card) else None),
        "email": (EMAIL.search(card).group(1) if EMAIL.search(card) else None),
        "address": (ADDRESS.search(card).group(1) if ADDRESS.search(card) else None),
        "scope": (SCOPE.search(card).group(1) if SCOPE.search(card) else None),
    }


def relevance(row):
    text_value = (row.get("scope") or "") + " " + row["raw"]
    score = 0
    score += sum(2 for word in PRODUCT_WORDS if word in text_value)
    if row.get("status") == "存续":
        score += 6
    elif row.get("status") in ("注销", "吊销"):
        score -= 8
    if any(word in text_value for word in MANUFACTURE_WORDS):
        score += 4
    if "个体工商户" in row["raw"]:
        score -= 3
    if row.get("region") and row["region"] in row["raw"]:
        score += 4
    return score


def merge_companies(result_rows, region):
    by_name = {}
    for block in result_rows:
        for card in block.get("cards", []):
            row = parse_card(card)
            if not row["name"]:
                continue
            row["queries"] = [block.get("query", "")]
            row["region"] = region
            existing = by_name.get(row["name"])
            if existing:
                existing["raw"] = max((existing["raw"], row["raw"]), key=len)
                if block.get("query") and block["query"] not in existing["queries"]:
                    existing["queries"].append(block["query"])
                for key in ("status", "legal", "capital", "date", "credit", "phone",
                            "email", "address", "scope"):
                    if not existing.get(key) and row.get(key):
                        existing[key] = row[key]
            else:
                by_name[row["name"]] = row
    ranked = []
    for name, row in by_name.items():
        row["score"] = relevance(row)
        ranked.append(row)
    ranked.sort(key=lambda item: -item["score"])
    return ranked


def pick_riskbird_link(search_row, name):
    for card in search_row.get("cards", []):
        for link in card.get("links", []):
            if name in (link.get("name") or ""):
                return link["url"]
    prefix = name[:6]
    for card in search_row.get("cards", []):
        for link in card.get("links", []):
            if prefix and prefix in (link.get("name") or "") and len(link.get("name") or "") <= len(name) + 6:
                return link["url"]
    return None


def riskbird_full_name(search_row, name):
    """Resolve card abbreviations to the detail-page full company name.

    The adapter compares the registry 企业名称 verbatim; a QCC card name like
    "徐州闽江商贸" fails IDENTITY_CONFLICT against the real page name
    "徐州闽江商贸有限公司" even when credit code and legal name match.
    """
    for card in search_row.get("cards", []):
        for link in card.get("links", []):
            link_name = link.get("name") or ""
            if name in link_name or link_name in name:
                return link_name
    return name


def parse_trademark_rows(raw_text):
    """Parse first-page trademark rows from the Riskbird IP tab text dump.

    The page renders each row as "序号\\t\\n\\t商标名称\\t国际分类\\t状态\\t注册号\\t日期";
    normalize the embedded tab-newline-tab sequence before matching.
    """
    normalized = (raw_text or "").replace("\t\n\t", "\t")
    rows = []
    for match in TRADEMARK_ROW.finditer(normalized):
        item = {
            "name": match.group(2).strip(),
            "class": match.group(3).strip(),
            "status": match.group(4).strip(),
            "reg_no": match.group(5),
            "applied": match.group(6),
        }
        if item["name"] and not item["name"].startswith("商标"):
            rows.append(item)
    return rows


def registered_trademarks(rows):
    return [row for row in rows if row.get("status") == "已注册"]


def relevant_trademarks(rows):
    return [row for row in rows
            if (row.get("class") or "").startswith(RELEVANT_TRADEMARK_CLASSES)]


def run_webcli(arguments, timeout=130):
    environment = os.environ.copy()
    environment["WEBCLI_WINDOW"] = "background"
    environment.pop("NODE_OPTIONS", None)
    result = subprocess.run(["o2", "launch", "webcli", *arguments],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, check=False, env=environment,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    message = (result.stdout or "") + (result.stderr or "")
    if result.returncode:
        raise RuntimeError(message.strip()[:400] or "WEBCLI_COMMAND_FAILED")
    payload = json.loads(result.stdout)
    if isinstance(payload, dict) and "data" in payload and "session" in payload:
        payload = payload["data"]
    return payload


def browser_eval(session, script, timeout=90):
    return run_webcli(["browser", session, "eval", script], timeout=timeout)


def collect_queries(queries, session, output_path, wait_open=8, wait_between=6):
    all_results = (json.loads(Path(output_path).read_text(encoding="utf-8"))
                   if Path(output_path).exists() else [])
    done = {row.get("query") for row in all_results}
    stopped = False
    for index, query in enumerate(queries, 1):
        if query in done:
            print(f"[{index}/{len(queries)}] cached {query}", flush=True)
            continue
        if stopped:
            break
        run_webcli(["browser", session, "open", QCC_SEARCH_URL.format(keyword=quote(query))])
        time.sleep(wait_open)
        payload = browser_eval(session, QCC_EXTRACT)
        print(f"[{index}/{len(queries)}] {query}: total={payload.get('total')} "
              f"cards={len(payload.get('cards', []))} challenge={payload.get('challenge')}", flush=True)
        all_results.append({"query": query, **payload})
        write_json(output_path, all_results)
        if payload.get("challenge"):
            print("CHALLENGE DETECTED - STOPPING", flush=True)
            stopped = True
            break
        time.sleep(wait_between)
    return all_results


def collect_riskbird_links(companies, session, output_path, limit=None, interval=20):
    saved = (json.loads(Path(output_path).read_text(encoding="utf-8"))
             if Path(output_path).exists() else [])
    done = {row.get("name") for row in saved}
    stopped = False
    for index, company in enumerate(companies[:limit], 1):
        name = company["name"]
        if name in done or stopped:
            continue
        if len(saved):
            time.sleep(interval)
        run_webcli(["browser", session, "open", RISKBIRD_SEARCH_URL.format(keyword=quote(name))])
        time.sleep(3)
        payload = browser_eval(session, RISKBIRD_SEARCH_EXTRACT)
        payload["name"] = name
        saved.append(payload)
        write_json(output_path, saved)
        print(f"{name}: blocked={payload.get('blocked')} cards={len(payload.get('cards', []))}", flush=True)
        if payload.get("blocked"):
            print("BLOCKED - STOPPING", flush=True)
            stopped = True
            break
    return saved


def verify_riskbird_details(companies, session, search_rows, output_path, interval=20, limit=None):
    search_by_name = {row.get("name"): row for row in search_rows}
    details = (json.loads(Path(output_path).read_text(encoding="utf-8"))
               if Path(output_path).exists() else [])
    done = {row.get("name") for row in details}
    for index, company in enumerate(companies[:limit], 1):
        name = company["name"]
        if name in done:
            continue
        url = pick_riskbird_link(search_by_name.get(name, {}), name)
        if not url:
            details.append({"name": name, "status": "not_found_in_riskbird"})
            write_json(output_path, details)
            print(f"{name}: NOT FOUND", flush=True)
            continue
        if index > 1:
            time.sleep(interval)
        company_name = riskbird_full_name(search_by_name.get(name, {}), name)
        arguments = ["riskbird", "company", url, "--company", company_name]
        if company.get("credit"):
            arguments.extend(["--credit-code", company["credit"]])
        if company.get("legal"):
            arguments.extend(["--legal-name", company["legal"]])
        try:
            payload = run_webcli(arguments)
        except RuntimeError as error:
            message = str(error)
            details.append({"name": name, "source_url": url, "status": "adapter_failed",
                            "error": message[:300]})
            write_json(output_path, details)
            print(f"{name}: ADAPTER FAILED {message[:120]}", flush=True)
            if "CHALLENGE" in message or "AUTH_REQUIRED" in message or "RATE_LIMITED" in message:
                print("STOP - RISK CONTROL", flush=True)
                break
            continue
        if isinstance(payload, list) and len(payload) == 1:
            payload = payload[0]
        details.append({"name": name, "source_url": url, "status": "ok", "detail": payload})
        write_json(output_path, details)
        print(f"{name}: OK", flush=True)
    return details


def read_riskbird_ip(companies, session, search_rows, output_path, interval=20, limit=None):
    """Read the Riskbird 知识产权 tab for brand/trademark prioritization.

    Uses the same entid-only detail URLs as verify. Only first-page rows are
    captured because the trademark table paginates; the total counts are read
    from the tab headers.
    """
    search_by_name = {row.get("name"): row for row in search_rows}
    saved = (json.loads(Path(output_path).read_text(encoding="utf-8"))
             if Path(output_path).exists() else [])
    done = {row.get("name") for row in saved}
    for company in companies[:limit] if limit else companies:
        name = company["name"]
        if name in done:
            continue
        url = pick_riskbird_link(search_by_name.get(name, {}), name)
        if not url:
            print(f"{name}: NOT FOUND - skipping IP read", flush=True)
            continue
        if saved:
            time.sleep(max(0, interval))
        run_webcli(["browser", session, "open", url])
        time.sleep(4)
        browser_eval(session, RISKBIRD_IP_CLICK_AND_READ)
        time.sleep(3)
        payload = browser_eval(session, RISKBIRD_IP_CLICK_AND_READ)
        rows = parse_trademark_rows(payload.get("rawTmText") or "")
        saved.append({
            "name": name,
            "header": {"url": payload.get("url"), "ipClicked": payload.get("ipClicked")},
            "ip": {
                "trademarkCount": payload.get("trademarkCount"),
                "patentCount": payload.get("patentCount"),
                "copyrightCount": payload.get("copyrightCount"),
                "trademarks": rows[:12],
                "registeredRelevant": relevant_trademarks(registered_trademarks(rows)),
            },
        })
        write_json(output_path, saved)
        print(f"{name}: tm={payload.get('trademarkCount')} pt={payload.get('patentCount')} "
              f"ipClicked={payload.get('ipClicked')}", flush=True)
    return saved


def main():
    parser = argparse.ArgumentParser(description="区域产业带走访发现：企查查检索 + 风鸟核验，零付费API")
    parser.add_argument("phase", choices=["collect", "parse", "verify", "trademarks"])
    parser.add_argument("--region", help="区域名，例如：邳州")
    parser.add_argument("--keywords", help="逗号分隔类目词，例如：沐浴,泡脚,木梳")
    parser.add_argument("--suffixes", default="", help="可选逗号分隔后缀，例如：制造,电子商务")
    parser.add_argument("--session", help="任务专属后台浏览器会话名")
    parser.add_argument("--out-dir", required=True, help="任务输出目录（绝对路径）")
    parser.add_argument("--results", default="qcc_results.json", help="collect 结果文件名")
    parser.add_argument("--companies", default="companies.json", help="parse 输出/verify 输入文件名")
    parser.add_argument("--search-file", default="riskbird_search.json")
    parser.add_argument("--details-file", default="riskbird_details.json")
    parser.add_argument("--ip-file", default="riskbird_ip.json")
    parser.add_argument("--limit", type=int, help="verify 阶段最多处理的企业数")
    parser.add_argument("--interval", type=float, default=20, help="风鸟导航最小间隔秒数")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.phase == "collect":
        if not args.session or not args.region:
            raise SystemExit("collect 需要 --session 和 --region")
        keywords = [item for item in (args.keywords or "").split(",") if item.strip()]
        suffixes = [item for item in args.suffixes.split(",") if item.strip()]
        queries = build_queries(args.region, keywords, suffixes)
        collect_queries(queries, args.session, out_dir / args.results)
    elif args.phase == "parse":
        if not args.region:
            raise SystemExit("parse 需要 --region")
        results_path = out_dir / args.results
        rows = json.loads(results_path.read_text(encoding="utf-8"))
        companies = merge_companies(rows, args.region)
        write_json(out_dir / args.companies, companies)
        print(f"unique companies: {len(companies)}")
    elif args.phase == "verify":
        if not args.session:
            raise SystemExit("verify 需要 --session")
        companies = json.loads((out_dir / args.companies).read_text(encoding="utf-8"))
        search_path = out_dir / args.search_file
        search_rows = collect_riskbird_links(companies, args.session, search_path,
                                              limit=args.limit, interval=max(0, args.interval))
        verify_riskbird_details(companies, args.session, search_rows,
                                out_dir / args.details_file,
                                interval=max(0, args.interval), limit=args.limit)
    elif args.phase == "trademarks":
        if not args.session:
            raise SystemExit("trademarks 需要 --session")
        companies = json.loads((out_dir / args.companies).read_text(encoding="utf-8"))
        search_rows = json.loads((out_dir / args.search_file).read_text(encoding="utf-8"))
        read_riskbird_ip(companies, args.session, search_rows, out_dir / args.ip_file,
                         interval=max(0, args.interval), limit=args.limit)


if __name__ == "__main__":
    main()
