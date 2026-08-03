#!/usr/bin/env python3
"""Update the ETF dashboard data for GitHub Pages.

The script keeps secrets out of generated JSON and source files. It uses the
iFinD refresh token from the environment and falls back to public exchange
quote aggregation when an entitled iFinD field is unavailable.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MASTER_FILE = DATA_DIR / "master.json"
DASHBOARD_FILE = DATA_DIR / "dashboard.json"
HISTORY_FILE = DATA_DIR / "market_history.json"

IFIND_BASE = "https://quantapi.51ifind.com/api/v1"
EASTMONEY_LIST_HOSTS = [
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://33.push2.eastmoney.com/api/qt/clist/get",
    "https://28.push2.eastmoney.com/api/qt/clist/get",
]


def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.2,
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
        "Connection": "close",
    })
    return session


SESSION = build_session()


def safe_number(value: Any, divisor: float = 1.0) -> float:
    try:
        if value in (None, "", "-", "--"):
            return 0.0
        return float(value) / divisor
    except (TypeError, ValueError):
        return 0.0


def atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def exchange_code(code: str) -> str:
    return f"{code}.SH" if code.startswith(("5", "6")) else f"{code}.SZ"


def fetch_universe() -> list[dict[str, Any]]:
    base_params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f6",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
        "fields": "f12,f14,f2,f3,f6,f20,f62,f184",
    }
    errors: list[str] = []
    diff: list[dict[str, Any]] = []
    for host in EASTMONEY_LIST_HOSTS:
        page_rows: list[dict[str, Any]] = []
        total = None
        for page in range(1, 80):
            params = {**base_params, "pn": page}
            page_diff: list[dict[str, Any]] = []
            for attempt in range(1, 4):
                try:
                    response = SESSION.get(host, params=params, timeout=(15, 45))
                    response.raise_for_status()
                    data = (response.json() or {}).get("data") or {}
                    total = int(data.get("total") or total or 0)
                    page_diff = data.get("diff") or []
                    if page_diff:
                        break
                    errors.append(f"{host} page {page}: empty response")
                except (requests.RequestException, ValueError) as exc:
                    errors.append(f"{host} page {page} attempt {attempt}: {type(exc).__name__}")
                    time.sleep(min(10, attempt * 2))
            if not page_diff:
                break
            page_rows.extend(page_diff)
            if (total and len(page_rows) >= total) or len(page_diff) < base_params["pz"]:
                break
            time.sleep(0.15)
        if page_rows:
            diff = page_rows
            print(
                f"ETF universe loaded from host {host.split('/')[2]}: "
                f"{len(diff)} rows across multiple pages"
            )
            break
    if not diff:
        cached = load_json(DASHBOARD_FILE, {}).get("etfs", [])
        if cached:
            print("All quote hosts unavailable; using last successful ETF snapshot", file=sys.stderr)
            return cached
        summary = "; ".join(errors[-8:])
        raise RuntimeError(f"All ETF quote hosts failed. Recent attempts: {summary}")
    rows: list[dict[str, Any]] = []
    for raw in diff:
        code = str(raw.get("f12") or "").strip()
        name = str(raw.get("f14") or "").strip()
        if len(code) != 6 or not name:
            continue
        rows.append({
            "code": exchange_code(code),
            "short_code": code,
            "name": name,
            "price": safe_number(raw.get("f2")),
            "change_pct": safe_number(raw.get("f3")),
            "turnover_yi": safe_number(raw.get("f6"), 100_000_000),
            "aum_yi": safe_number(raw.get("f20"), 100_000_000),
            "estimated_flow_yi": safe_number(raw.get("f62"), 100_000_000),
            "flow_ratio_pct": safe_number(raw.get("f184")),
        })
    if not rows:
        raise RuntimeError("ETF universe returned no rows")
    return rows


def get_ifind_access_token(refresh_token: str) -> str:
    response = SESSION.post(
        f"{IFIND_BASE}/get_access_token",
        headers={"refresh_token": refresh_token},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    token = (
        payload.get("access_token")
        or (payload.get("data") or {}).get("access_token")
        or (payload.get("data") or {}).get("accessToken")
    )
    if not token:
        raise RuntimeError("iFinD did not return an access token")
    return str(token)


def flatten_ifind_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [payload.get("data"), payload.get("tables"), payload.get("table")]
    output: list[dict[str, Any]] = []
    while candidates:
        item = candidates.pop(0)
        if isinstance(item, list):
            if item and all(isinstance(row, dict) for row in item):
                output.extend(item)
            else:
                candidates.extend(item)
        elif isinstance(item, dict):
            for key in ("tables", "table", "data", "rows"):
                if key in item:
                    candidates.append(item[key])
    return output


def update_ifind_quotes(rows: list[dict[str, Any]], access_token: str) -> bool:
    by_code = {row["code"]: row for row in rows}
    updated = False
    for offset in range(0, len(rows), 80):
        codes = ",".join(row["code"] for row in rows[offset:offset + 80])
        response = SESSION.post(
            f"{IFIND_BASE}/real_time_quotation",
            headers={"access_token": access_token, "Content-Type": "application/json"},
            json={"codes": codes, "indicators": "latest,changeRatio,amount"},
            timeout=45,
        )
        if not response.ok:
            continue
        for item in flatten_ifind_rows(response.json()):
            code = str(item.get("thscode") or item.get("code") or "")
            target = by_code.get(code)
            if not target:
                continue
            latest = item.get("latest")
            change = item.get("changeRatio")
            amount = item.get("amount")
            if latest not in (None, "", "-"):
                target["price"] = safe_number(latest)
                updated = True
            if change not in (None, "", "-"):
                target["change_pct"] = safe_number(change)
            if amount not in (None, "", "-"):
                target["turnover_yi"] = safe_number(amount, 100_000_000)
        time.sleep(0.12)
    return updated


def normalize_index_name(name: str) -> str:
    text = re.sub(r"\s+", "", name or "")
    text = text.replace("收益率", "").replace("价格指数", "")
    return text or "未分类"


def infer_provider(index_name: str) -> str:
    if index_name.startswith(("沪深", "中证", "上证", "科创")):
        return "中证指数"
    if index_name.startswith(("深证", "创业板")):
        return "深圳证券信息"
    if index_name.startswith("国证"):
        return "国证指数"
    if index_name.startswith("恒生"):
        return "恒生指数"
    return "其他"


def infer_theme(index_name: str) -> dict[str, str]:
    """Assign a reviewable exposure family while preserving the exact index."""
    name = normalize_index_name(index_name)
    compact = re.sub(r"[\s&·（）()/-]", "", name).lower()

    def result(level1: str, family: str, confidence: str, reason: str) -> dict[str, str]:
        scope = "跨境" if any(word in compact for word in (
            "港股", "香港", "恒生", "纳斯达克", "标普", "日经", "东京",
            "海外", "中韩", "韩交所", "富时", "msci",
        )) else "境内"
        return {
            "theme_level1": level1,
            "theme_family": family,
            "theme_confidence": confidence,
            "theme_reason": reason,
            "market_scope": scope,
        }

    rules = [
        (("半导体",), ("设备", "材料", "产品与设备"), "科技", "半导体设备与材料", "高", "半导体且包含设备/材料/产品与设备"),
        (("芯片", "半导体"), (), "科技", "半导体芯片", "高", "名称直接包含芯片或半导体"),
        (("人工智能", "ai", "算力", "云计算", "大数据"), (), "科技", "人工智能与算力", "高", "名称包含AI/算力/云计算/大数据"),
        (("机器人", "智能制造", "工业母机", "自动化"), (), "科技", "机器人与智能制造", "高", "名称包含机器人/智能制造/自动化"),
        (("通信设备", "5g", "卫星通信"), (), "科技", "通信设备与5G", "高", "名称包含通信设备/5G/卫星通信"),
        (("软件",), (), "科技", "软件与信息服务", "高", "名称包含软件"),
        (("互联网",), (), "科技", "互联网平台", "高", "名称包含互联网"),
        (("信息技术", "科技"), (), "科技", "综合科技", "中", "泛信息技术/科技指数"),
        (("动漫", "游戏", "传媒"), (), "传媒", "传媒与游戏", "高", "名称包含传媒/动漫/游戏"),
        (("创新药", "生物医药", "生物科技"), (), "医药", "创新药与生物科技", "高", "名称包含创新药/生物医药/生物科技"),
        (("医疗器械", "医疗设备"), (), "医药", "医疗器械", "高", "名称包含医疗器械/设备"),
        (("医药", "医疗", "中药"), (), "医药", "医药医疗综合", "中", "泛医药医疗指数"),
        (("光伏", "太阳能"), (), "新能源", "光伏", "高", "名称包含光伏/太阳能"),
        (("电池", "储能"), (), "新能源", "电池与储能", "高", "名称包含电池/储能"),
        (("新能源汽车", "智能汽车", "汽车"), (), "新能源", "汽车与智能驾驶", "中", "名称包含汽车/智能驾驶"),
        (("电网", "电力设备"), (), "新能源", "电网与电力设备", "高", "名称包含电网/电力设备"),
        (("风电", "新能源"), (), "新能源", "新能源综合", "中", "泛新能源指数"),
        (("证券公司", "证券投资", "券商"), (), "金融", "证券", "高", "名称包含证券/券商"),
        (("银行",), (), "金融", "银行", "高", "名称包含银行"),
        (("保险",), (), "金融", "保险", "高", "名称包含保险"),
        (("金融科技",), (), "金融", "金融科技", "高", "名称包含金融科技"),
        (("酒", "白酒"), (), "消费", "酒类", "高", "名称包含酒类"),
        (("食品", "饮料"), (), "消费", "食品饮料", "高", "名称包含食品/饮料"),
        (("消费", "家电", "旅游", "零售"), (), "消费", "消费综合", "中", "泛消费指数"),
        (("黄金产业", "黄金9999", "黄金"), (), "资源品", "黄金与贵金属", "高", "名称包含黄金"),
        (("有色", "稀土"), (), "资源品", "有色金属与稀土", "高", "名称包含有色/稀土"),
        (("煤炭",), (), "资源品", "煤炭", "高", "名称包含煤炭"),
        (("oilgas", "油气", "石油"), (), "资源品", "石油天然气", "高", "名称包含油气/石油"),
        (("化工", "钢铁"), (), "资源品", "基础材料", "中", "名称包含化工/钢铁"),
        (("军工", "国防", "航空航天"), (), "工业", "国防军工", "高", "名称包含军工/国防/航空航天"),
        (("机械", "基建", "建筑", "运输", "航运", "物流"), (), "工业", "工业与基建", "中", "泛工业/基建指数"),
        (("红利低波", "低波红利"), (), "策略", "红利低波", "高", "同时包含红利和低波"),
        (("红利", "股息"), (), "策略", "红利", "高", "名称包含红利/股息"),
        (("低波",), (), "策略", "低波动", "高", "名称包含低波"),
        (("价值", "质量", "成长", "动量"), (), "策略", "风格与多因子", "中", "名称包含价值/质量/成长/动量"),
        (("低碳", "esg", "碳中和"), (), "策略", "ESG与低碳", "高", "名称包含ESG/低碳/碳中和"),
    ]
    for any_words, required_words, level1, family, confidence, reason in rules:
        if any(word.lower() in compact for word in any_words) and (
            not required_words or any(word.lower() in compact for word in required_words)
        ):
            return result(level1, family, confidence, reason)

    broad_rules = [
        (("沪深300",), "宽基", "沪深300"),
        (("中证a500",), "宽基", "中证A500"),
        (("中证500",), "宽基", "中证500"),
        (("中证1000",), "宽基", "中证1000"),
        (("中证2000",), "宽基", "中证2000"),
        (("上证50",), "宽基", "上证50"),
        (("科创50", "科创板50"), "宽基", "科创50"),
        (("科创创业50",), "宽基", "科创创业50"),
        (("创业板50",), "宽基", "创业板50"),
        (("创业板",), "宽基", "创业板"),
        (("上证综合",), "宽基", "上证综指"),
        (("恒生科技",), "跨境宽基", "恒生科技"),
        (("恒生指数",), "跨境宽基", "恒生指数"),
        (("纳斯达克100",), "跨境宽基", "纳斯达克100"),
        (("日经225",), "跨境宽基", "日经225"),
    ]
    for words, level1, family in broad_rules:
        if any(word.lower() in compact for word in words):
            return result(level1, family, "高", f"名称直接对应{family}")
    if "无跟踪标的" in name:
        return result("其他资产", "现金与主动管理ETF", "中", "基金资料显示无跟踪标的")
    return result("待审核", "其他/待人工归类", "低", "未命中现有主题规则")


def fetch_f10_metadata(code: str) -> dict[str, str]:
    url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
    response = SESSION.get(url, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    def value_after(label: str) -> str:
        cell = soup.find(string=lambda text: isinstance(text, str) and text.strip() == label)
        if not cell:
            return ""
        parent = cell.parent
        target = parent.find_next("td") if parent else None
        return target.get_text(" ", strip=True) if target else ""

    manager = value_after("基金管理人")
    index_name = value_after("跟踪标的")
    return {
        "manager": manager or "待补充管理人",
        "index_name": normalize_index_name(index_name),
        "index_provider": infer_provider(index_name),
        "index_code": "",
    }


def enrich_metadata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cached_list = load_json(MASTER_FILE, [])
    cache = {item.get("code"): item for item in cached_list if item.get("code")}
    missing = [row for row in rows if row["code"] not in cache]
    refresh_all = os.getenv("FULL_METADATA_REFRESH", "").lower() == "true"
    targets = rows if refresh_all else missing
    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_map = {
                executor.submit(fetch_f10_metadata, row["short_code"]): row["code"]
                for row in targets
            }
            for future in concurrent.futures.as_completed(future_map):
                code = future_map[future]
                try:
                    cache[code] = {"code": code, **future.result()}
                except Exception as exc:
                    print(f"metadata warning {code}: {exc}", file=sys.stderr)
                    cache.setdefault(code, {
                        "code": code,
                        "manager": "待补充管理人",
                        "index_name": "未分类",
                        "index_provider": "其他",
                        "index_code": "",
                    })
    for row in rows:
        row.update(cache.get(row["code"], {}))
        row["manager"] = row.get("manager") or "待补充管理人"
        row["index_name"] = normalize_index_name(row.get("index_name") or "")
        row["index_provider"] = row.get("index_provider") or infer_provider(row["index_name"])
        row["index_code"] = row.get("index_code") or ""
    master = [{
        key: row.get(key, "")
        for key in ("code", "short_code", "name", "manager", "index_provider", "index_code", "index_name")
    } for row in rows]
    atomic_write(MASTER_FILE, master)
    return rows


def aggregate(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    index_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    manager_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        exact_key = row["index_code"] or normalize_index_name(row["index_name"])
        index_groups[(row["index_provider"], exact_key)].append(row)
        manager_groups[row["manager"]].append(row)

    indices = []
    for (provider, _), items in index_groups.items():
        flow = sum(item["estimated_flow_yi"] for item in items)
        indices.append({
            "index_provider": provider,
            "index_code": next((item["index_code"] for item in items if item["index_code"]), ""),
            "index_name": items[0]["index_name"],
            "etf_count": len(items),
            "manager_count": len({item["manager"] for item in items}),
            "aum_yi": round(sum(item["aum_yi"] for item in items), 4),
            "turnover_yi": round(sum(item["turnover_yi"] for item in items), 4),
            "estimated_flow_yi": round(flow, 4),
            "flow_5d_yi": 0,
            "flow_strength": 0,
        })
    indices.sort(key=lambda item: item["estimated_flow_yi"], reverse=True)
    max_flow = max([abs(item["estimated_flow_yi"]) for item in indices] or [1])
    for item in indices:
        item["flow_strength"] = round(abs(item["estimated_flow_yi"]) / max_flow * 100, 2)

    managers = []
    for manager, items in manager_groups.items():
        managers.append({
            "manager": manager,
            "etf_count": len(items),
            "aum_yi": round(sum(item["aum_yi"] for item in items), 4),
            "turnover_yi": round(sum(item["turnover_yi"] for item in items), 4),
            "estimated_flow_yi": round(sum(item["estimated_flow_yi"] for item in items), 4),
            "bar_width": 0,
        })
    managers.sort(key=lambda item: item["estimated_flow_yi"], reverse=True)
    max_manager_flow = max([abs(item["estimated_flow_yi"]) for item in managers] or [1])
    for item in managers:
        item["bar_width"] = round(abs(item["estimated_flow_yi"]) / max_manager_flow * 100, 2)
    theme_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in indices:
        theme = infer_theme(item["index_name"])
        item.update(theme)
        theme_groups[(theme["theme_level1"], theme["theme_family"])].append(item)

    themes = []
    for (level1, family), items in theme_groups.items():
        member_etfs = [
            row for row in rows
            if infer_theme(row["index_name"])["theme_family"] == family
            and infer_theme(row["index_name"])["theme_level1"] == level1
        ]
        themes.append({
            "theme_level1": level1,
            "theme_family": family,
            "index_count": len(items),
            "etf_count": len(member_etfs),
            "manager_count": len({row["manager"] for row in member_etfs}),
            "aum_yi": round(sum(item["aum_yi"] for item in items), 4),
            "turnover_yi": round(sum(item["turnover_yi"] for item in items), 4),
            "estimated_flow_yi": round(sum(item["estimated_flow_yi"] for item in items), 4),
            "flow_5d_yi": round(sum(item["flow_5d_yi"] for item in items), 4),
            "exact_indices": [item["index_name"] for item in items],
            "review_required": any(item["theme_confidence"] != "高" for item in items),
            "flow_strength": 0,
        })
    themes.sort(key=lambda item: item["estimated_flow_yi"], reverse=True)
    max_theme_flow = max([abs(item["estimated_flow_yi"]) for item in themes] or [1])
    for item in themes:
        item["flow_strength"] = round(abs(item["estimated_flow_yi"]) / max_theme_flow * 100, 2)
    return indices, managers, themes


def update_history(metrics: dict[str, Any], generated_at: str) -> tuple[list[dict[str, Any]], str]:
    history = load_json(HISTORY_FILE, [])
    history.append({"observed_at": generated_at, **metrics})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=366)
    kept = []
    for item in history:
        try:
            observed = dt.datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            if observed >= cutoff:
                kept.append(item)
        except (KeyError, ValueError):
            continue
    atomic_write(HISTORY_FILE, kept)
    history_start = kept[0]["observed_at"][:10] if kept else generated_at[:10]
    return kept, history_start


def main() -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    rows = fetch_universe()
    source_parts = ["全量ETF列表/资金流估算"]
    refresh_token = os.environ.get("IFIND_REFRESH_TOKEN", "").strip()
    if refresh_token:
        try:
            access_token = get_ifind_access_token(refresh_token)
            if update_ifind_quotes(rows, access_token):
                source_parts.append("iFinD实时行情")
            else:
                source_parts.append("iFinD已连接，实时字段待授权")
        except Exception as exc:
            print(f"iFinD warning: {exc}", file=sys.stderr)
            source_parts.append("iFinD连接失败，保留公开行情")
    else:
        source_parts.append("未配置iFinD")

    rows = enrich_metadata(rows)
    indices, managers, themes = aggregate(rows)
    metrics = {
        "etf_count": len(rows),
        "turnover_yi": round(sum(row["turnover_yi"] for row in rows), 4),
        "estimated_flow_yi": round(sum(row["estimated_flow_yi"] for row in rows), 4),
        "inflow_count": sum(1 for row in rows if row["estimated_flow_yi"] > 0),
        "average_change_pct": round(sum(row["change_pct"] for row in rows) / max(1, len(rows)), 4),
        "aum_yi": round(sum(row["aum_yi"] for row in rows), 4),
    }
    _, history_start = update_history(metrics, generated_at)
    payload = {
        "schema_version": 1,
        "generated_at": generated_at,
        "history_start": history_start,
        "source_label": " + ".join(source_parts),
        "metrics": metrics,
        "indices": indices,
        "themes": themes,
        "managers": managers,
        "etfs": rows,
    }
    atomic_write(DASHBOARD_FILE, payload)
    print(
        f"Updated {len(rows)} ETFs, {len(indices)} exact index groups, "
        f"{len(themes)} theme families, {len(managers)} managers"
    )


if __name__ == "__main__":
    main()
