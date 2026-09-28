import html
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


RISK_TERMS = ["验证码拦截", "访问被拒绝", "请按照说明进行验证", "滑动验证", "登录后查看"]


class BrowserTransientError(RuntimeError):
    pass


def clean_title(value):
    return html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).strip()


def normalize(value):
    return re.sub(r"\s+", "", clean_title(value)).lower()


def compile_union(patterns):
    usable = [pattern for pattern in patterns or [] if pattern]
    return re.compile("|".join(f"(?:{pattern})" for pattern in usable), re.I) if usable else None


def classify_item(title, job):
    text = clean_title(title)
    include = compile_union(job.get("include_patterns"))
    accessory = compile_union(job.get("accessory_patterns"))
    electric = compile_union(job.get("electric_patterns"))
    exclude = compile_union(job.get("exclude_patterns"))
    category_match = bool(include and include.search(text))
    is_accessory = bool(accessory and accessory.search(text))
    is_electric = bool(electric and electric.search(text))
    is_excluded = bool(exclude and exclude.search(text))
    relevant = category_match and not is_accessory and not is_excluded
    if job.get("exclude_electric"):
        relevant = relevant and not is_electric
    return {
        "relevant": relevant,
        "category_match": category_match,
        "electric": is_electric,
        "accessory": is_accessory,
        "excluded": is_excluded,
    }


def is_tmall_item(item):
    aliases = {part.strip().lower() for part in str(item.get("iconList") or "").split(",")}
    url = str(item.get("auctionURL") or "").lower()
    return "tmall" in aliases or "tmallpc" in aliases or "detail.tmall.com" in url


def platform_name(item):
    return "天猫" if is_tmall_item(item) else "淘宝"


def parse_sales_lower_bound(value):
    text = clean_title(value).replace(",", "")
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if match:
        return int(float(match.group(1)) * 10000)
    match = re.search(r"(\d+)\s*\+?", text)
    return int(match.group(1)) if match else None


def run_o2(session, command, *args, timeout=90, transient_retries=1):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = None
    for attempt in range(transient_retries + 1):
        try:
            completed = subprocess.run(
                ["o2", "launch", "webcli", "browser", session, command, *args],
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            message = (completed.stderr or completed.stdout).strip()
        except subprocess.TimeoutExpired:
            message = f"webcli timed out after {timeout} seconds"
            completed = None
        transient = any(term in message.lower() for term in ["operation was aborted", "timed out", "timeout", "bridge disconnected", "connection reset"])
        if completed is not None and completed.returncode == 0:
            break
        if not transient or attempt >= transient_retries:
            if transient:
                raise BrowserTransientError(message)
            raise RuntimeError(message)
        time.sleep(2 ** attempt)
    if completed is None:
        raise RuntimeError("webcli failed without a process result")
    output = completed.stdout.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return output


def open_search(session, query):
    return run_o2(session, "open", f"https://s.taobao.com/search?q={quote(query)}")


def make_search_script(query, pages):
    query_json = json.dumps(query, ensure_ascii=False)
    return (
        "(async()=>{"
        "const text=document.body?document.body.innerText:'';"
        "const hiddenChallenge=Boolean(document.querySelector('iframe[src*=\"_____tmd__\"]'));"
        f"if(hiddenChallenge||{json.dumps(RISK_TERMS, ensure_ascii=False)}.some(function(t){{return text.includes(t)}})){{throw new Error('TAOBAO_RISK_CONTROL')}};"
        "const u=performance.getEntriesByType('resource').map(function(e){return e.name}).find(function(x){return x.includes('appId%22%3A%2234385')});"
        "if(!u){throw new Error('search template missing; open a Taobao search result page first')};"
        "const outer=JSON.parse(new URL(u).searchParams.get('data'));const base=JSON.parse(outer.params);const out=[];"
        f"for(let page=1;page<={int(pages)};page++){{const p=Object.assign({{}},base);"
        f"Object.assign(p,{{q:encodeURIComponent({query_json}),page:page,n:48,pageSize:48,p4pIds:null,p4pS:null,itemIds:null,bcoffset:'',ntoffset:''}});"
        "const request=lib.mtop.request({api:'mtop.relationrecommend.WirelessRecommend.recommend',v:'2.0',needLogin:false,data:{appId:'34385',params:JSON.stringify(p)}});"
        "const requestTimeout=new Promise(function(_,reject){setTimeout(function(){reject(new Error('MTOP_REQUEST_TIMEOUT'))},20000)});"
        "const r=await Promise.race([request,requestTimeout]);"
        "const items=r.data&&r.data.itemsArray?r.data.itemsArray:[];items.forEach(function(x){out.push({query:"
        + query_json
        + ",page:page,item_id:String(x.item_id||''),title:x.title||'',shop:x.shopInfo?x.shopInfo.title:'',shop_url:x.shopInfo?x.shopInfo.url:'',user_id:String(x.userId||''),sales:x.realSales||'',iconList:x.iconList||'',auctionURL:x.auctionURL||'',leafCategory:String(x.leafCategory||'')})});"
        "if(items.length<48){break};await new Promise(function(resolve){setTimeout(resolve,3000)});};return out})()"
    )


def ensure_job_dir(path):
    job_dir = Path(path).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def load_job(job_dir):
    path = Path(job_dir) / "job.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run create_job.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
