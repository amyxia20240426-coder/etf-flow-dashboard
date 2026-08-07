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
import math
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


def fetch_universe(observed_at: str) -> tuple[list[dict[str, Any]], str, bool]:
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
        complete = False
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
                complete = True
                break
            time.sleep(0.15)
        if page_rows and complete:
            diff = page_rows
            print(
                f"ETF universe loaded from host {host.split('/')[2]}: "
                f"{len(diff)} rows across multiple pages"
            )
            break
        if page_rows:
            errors.append(
                f"{host}: incomplete pagination {len(page_rows)}/{total or 'unknown'}; discarded"
            )
    if not diff:
        cached_dashboard = load_json(DASHBOARD_FILE, {})
        cached = cached_dashboard.get("etfs", [])
        if cached:
            print("All quote hosts unavailable; using last successful ETF snapshot", file=sys.stderr)
            cached_time = (
                ((cached_dashboard.get("as_of") or {}).get("intraday_flow") or {}).get("time")
                or cached_dashboard.get("generated_at")
                or observed_at
            )
            return cached, str(cached_time), False
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
    return rows, observed_at, True


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


def ifind_error(payload: dict[str, Any]) -> str:
    code = payload.get("errorcode", payload.get("errorCode", 0))
    if code in (None, 0, "0"):
        return ""
    return str(payload.get("errmsg") or payload.get("errorMsg") or f"errorcode={code}")


def parse_ifind_date_sequence(
    payload: dict[str, Any], indicators: tuple[str, str]
) -> list[dict[str, Any]]:
    """Normalize the common row- and column-oriented iFinD JSON shapes."""
    share_indicator, nav_indicator = indicators
    aliases = {
        "code": ("thscode", "thsCode", "code", "securityCode"),
        "date": ("time", "date", "tradeDate", "tradedate"),
        "share": (share_indicator, "share"),
        "nav": (nav_indicator, "nav"),
    }
    rows: list[dict[str, Any]] = []

    def first(mapping: dict[str, Any], names: tuple[str, ...], default: Any = None) -> Any:
        return next((mapping[name] for name in names if name in mapping), default)

    def add_row(mapping: dict[str, Any], parent_code: str = "", parent_date: str = "") -> None:
        code = str(first(mapping, aliases["code"], parent_code) or parent_code)
        date_value = first(mapping, aliases["date"], parent_date) or parent_date
        date_text = str(date_value)[:10]
        if re.fullmatch(r"\d{8}", date_text):
            date_text = f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
        share = first(mapping, aliases["share"])
        nav = first(mapping, aliases["nav"])
        if code and date_value and share not in (None, "", "-", "--"):
            rows.append({
                "code": code,
                "date": date_text,
                "share": safe_number(share),
                "nav": safe_number(nav),
            })

    def visit(node: Any, parent_code: str = "", parent_times: list[Any] | None = None) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, parent_code, parent_times)
            return
        if not isinstance(node, dict):
            return
        code = str(first(node, aliases["code"], parent_code) or parent_code)
        times_value = first(node, aliases["date"], parent_times)
        times = times_value if isinstance(times_value, list) else parent_times

        # Column-oriented tables: {time:[...], indicator:[...]}.
        columns = {
            key: value for key, value in node.items()
            if isinstance(value, list) and key not in ("tables", "rows", "data")
        }
        if columns and any(key in columns for key in aliases["share"]):
            length = max((len(value) for value in columns.values()), default=0)
            for index in range(length):
                row = {
                    key: value[index] if index < len(value) else None
                    for key, value in columns.items()
                }
                if times and "time" not in row and index < len(times):
                    row["time"] = times[index]
                add_row(row, code)
        elif not any(isinstance(value, (dict, list)) for value in node.values()):
            add_row(node, code)

        for key in ("tables", "table", "data", "rows"):
            if key in node:
                visit(node[key], code, times)

    visit(payload)
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        unique[(row["code"], row["date"])] = row
    return list(unique.values())


def infer_share_multiplier(points: list[dict[str, Any]], current_aum_yi: float) -> float:
    latest = next(
        (item for item in reversed(points) if item.get("share", 0) > 0 and item.get("nav", 0) > 0),
        None,
    )
    if not latest or current_aum_yi <= 0:
        return 1.0
    candidates = (1.0, 10_000.0, 100_000_000.0)
    def distance(multiplier: float) -> float:
        calculated = latest["share"] * multiplier * latest["nav"] / 100_000_000
        return abs(math.log10(max(calculated, 1e-12) / current_aum_yi))
    return min(candidates, key=distance)


def calculate_subscription_flows(
    series_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in series_rows:
        by_code[item["code"]].append(item)
    current_aum = {item["code"]: safe_number(item.get("aum_yi")) for item in current_rows}
    recent_by_etf: dict[str, list[dict[str, Any]]] = {}
    market_by_date: dict[str, dict[str, Any]] = {}
    for code, points in by_code.items():
        points.sort(key=lambda item: item["date"])
        multiplier = infer_share_multiplier(points, current_aum.get(code, 0))
        flows: list[dict[str, Any]] = []
        previous = None
        for point in points:
            if previous and point["share"] > 0 and point["nav"] > 0:
                try:
                    gap_days = (
                        dt.date.fromisoformat(point["date"])
                        - dt.date.fromisoformat(previous["date"])
                    ).days
                except ValueError:
                    gap_days = 999
                if gap_days > 10:
                    previous = point
                    continue
                share_ratio = point["share"] / max(previous["share"], 1e-12)
                nav_ratio = point["nav"] / max(previous["nav"], 1e-12)
                # Unit splits/consolidations change shares and NAV inversely and
                # must not be counted as subscriptions or redemptions.
                is_unit_event = (
                    (share_ratio > 1.5 or share_ratio < 0.67)
                    and 0.85 <= share_ratio * nav_ratio <= 1.15
                )
                flow = 0.0 if is_unit_event else (
                    (point["share"] - previous["share"])
                    * multiplier * point["nav"] / 100_000_000
                )
                # Reject obviously broken units without silently turning them into zero.
                aum = current_aum.get(code, 0)
                if not aum or abs(flow) <= max(20.0, aum * 1.2):
                    entry = {
                        "date": point["date"],
                        "net_subscription_yi": round(flow, 6),
                    }
                    flows.append(entry)
                    bucket = market_by_date.setdefault(point["date"], {
                        "date": point["date"],
                        "net_subscription_yi": 0.0,
                        "inflow_count": 0,
                        "outflow_count": 0,
                        "etf_count": 0,
                    })
                    bucket["net_subscription_yi"] += flow
                    bucket["etf_count"] += 1
                    bucket["inflow_count"] += int(flow > 0)
                    bucket["outflow_count"] += int(flow < 0)
            previous = point
        recent_by_etf[code] = flows[-25:]
    market = []
    for item in sorted(market_by_date.values(), key=lambda value: value["date"]):
        item["net_subscription_yi"] = round(item["net_subscription_yi"], 4)
        item["source"] = "iFinD ETF份额变化 × 当日净值"
        market.append(item)
    maximum_coverage = max((item["etf_count"] for item in market), default=0)
    if maximum_coverage >= 500:
        market = [item for item in market if item["etf_count"] >= maximum_coverage * 0.8]
    return market[-366:], recent_by_etf


def merge_subscription_history(
    previous_market: list[dict[str, Any]],
    previous_etf: dict[str, list[dict[str, Any]]],
    new_market: list[dict[str, Any]],
    new_etf: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    market = {item["date"]: item for item in previous_market if item.get("date")}
    market.update({item["date"]: item for item in new_market if item.get("date")})
    merged_etf: dict[str, list[dict[str, Any]]] = {}
    for code in set(previous_etf) | set(new_etf):
        items = {
            item["date"]: item
            for item in [*(previous_etf.get(code) or []), *(new_etf.get(code) or [])]
            if item.get("date")
        }
        merged_etf[code] = [items[key] for key in sorted(items)][-25:]
    return [market[key] for key in sorted(market)][-366:], merged_etf


def fetch_ifind_subscription_history(
    rows: list[dict[str, Any]], access_token: str, previous: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    share_indicator = os.getenv("IFIND_ETF_SHARE_INDICATOR", "ths_share_fund").strip()
    nav_indicator = os.getenv("IFIND_ETF_NAV_INDICATOR", "ths_nav_fund").strip()
    previous_market = previous.get("daily_subscription_history") or []
    previous_etf = previous.get("daily_subscription_by_etf") or {}
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    start = today - dt.timedelta(days=366 if not previous_market else 14)
    day_count = max(1, (today - start).days + 1)
    batch_size = max(5, min(80, 9000 // max(1, day_count)))
    headers = {
        "access_token": access_token,
        "Content-Type": "application/json",
        "ifindlang": "cn",
    }
    probe_code = next(
        (item["code"] for item in rows if item["code"] == "510300.SH"),
        rows[0]["code"],
    )
    probe_start = today - dt.timedelta(days=45)
    share_params, share_function = probe_ifind_date_indicator(
        access_token, probe_code, share_indicator, probe_start, today
    )
    nav_params, nav_function = probe_ifind_date_indicator(
        access_token, probe_code, nav_indicator, probe_start, today
    )
    collected_map: dict[tuple[str, str], dict[str, Any]] = {}
    for indicator, value_name, params, functionpara in (
        (share_indicator, "share", share_params, share_function),
        (nav_indicator, "nav", nav_params, nav_function),
    ):
        for offset in range(0, len(rows), batch_size):
            codes = ",".join(item["code"] for item in rows[offset:offset + batch_size])
            response = SESSION.post(
                f"{IFIND_BASE}/date_sequence",
                headers=headers,
                json={
                    "codes": codes,
                    "startdate": start.strftime("%Y%m%d"),
                    "enddate": today.strftime("%Y%m%d"),
                    "functionpara": functionpara,
                    "indipara": [indicator_request(indicator, params)],
                },
                timeout=(20, 90),
            )
            response.raise_for_status()
            payload = response.json()
            error = ifind_error(payload)
            if error:
                raise RuntimeError(f"iFinD历史指标 {indicator} 返回：{error}")
            # Treat the requested single indicator as the parser's share column,
            # then merge share and NAV on code/date.
            batch_rows = parse_ifind_date_sequence(payload, (indicator, "__unused__"))
            if not batch_rows:
                raise RuntimeError(f"iFinD历史指标 {indicator} 返回成功但没有可解析数据")
            for item in batch_rows:
                key = (item["code"], item["date"])
                target = collected_map.setdefault(key, {
                    "code": item["code"], "date": item["date"], "share": 0.0, "nav": 0.0,
                })
                target[value_name] = item["share"]
            time.sleep(0.15)
    collected = list(collected_map.values())
    new_market, new_etf = calculate_subscription_flows(collected, rows)
    if not previous_market and len(new_market) < 60:
        raise RuntimeError(
            "iFinD返回的基金份额频率不足，不能作为ETF日度份额；请向iFinD确认ETF每日份额指标权限"
        )
    market, by_etf = merge_subscription_history(
        previous_market, previous_etf, new_market, new_etf
    )
    if not market:
        raise RuntimeError("iFinD历史数据已返回，但无法形成有效的ETF净申购赎回序列")
    status = {
        "available": True,
        "last_date": market[-1]["date"],
        "history_start": market[0]["date"],
        "method": "ETF份额日变化 × 当日单位净值",
        "share_indicator": share_indicator,
        "nav_indicator": nav_indicator,
        "coverage_etf_count": market[-1]["etf_count"],
    }
    return market, by_etf, status


def attach_subscription_metrics(
    rows: list[dict[str, Any]], by_etf: dict[str, list[dict[str, Any]]]
) -> None:
    for row in rows:
        history = by_etf.get(row["code"]) or []
        row["net_subscription_1d_yi"] = round(
            history[-1]["net_subscription_yi"], 4
        ) if history else 0.0
        row["net_subscription_5d_yi"] = round(
            sum(item["net_subscription_yi"] for item in history[-5:]), 4
        )


def payload_has_indicator_values(payload: Any, indicator: str) -> bool:
    if isinstance(payload, list):
        return any(payload_has_indicator_values(item, indicator) for item in payload)
    if not isinstance(payload, dict):
        return False
    if indicator in payload:
        value = payload[indicator]
        if isinstance(value, list):
            return any(item not in (None, "", "-", "--") for item in value)
        return value not in (None, "", "-", "--")
    return any(payload_has_indicator_values(value, indicator) for value in payload.values())


def indicator_request(indicator: str, params: list[str] | None) -> dict[str, Any]:
    item: dict[str, Any] = {"indicator": indicator}
    if params is not None:
        item["indiparams"] = params
    return item


def probe_ifind_date_indicator(
    access_token: str, code: str, indicator: str, start: dt.date, end: dt.date
) -> tuple[list[str] | None, dict[str, str]]:
    headers = {
        "access_token": access_token,
        "Content-Type": "application/json",
        "ifindlang": "cn",
    }
    parameter_variants: list[list[str] | None] = [
        None, [""], ["100"], ["", "100"], ["", "", ""], ["", "100", ""],
    ]
    function_variants = [
        {"Days": "Tradedays", "Fill": "Blank"},
        {"Days": "Tradedays", "Fill": "Blank", "Interval": "D"},
        {"Days": "Tradedays", "Fill": "Omit"},
    ]
    errors = []
    for functionpara in function_variants:
        for params in parameter_variants:
            response = SESSION.post(
                f"{IFIND_BASE}/date_sequence",
                headers=headers,
                json={
                    "codes": code,
                    "startdate": start.strftime("%Y%m%d"),
                    "enddate": end.strftime("%Y%m%d"),
                    "functionpara": functionpara,
                    "indipara": [indicator_request(indicator, params)],
                },
                timeout=(20, 60),
            )
            if not response.ok:
                errors.append(f"HTTP {response.status_code}")
                continue
            try:
                payload = response.json()
            except ValueError:
                errors.append("响应不是JSON")
                continue
            error = ifind_error(payload)
            if not error and payload_has_indicator_values(payload, indicator):
                print(
                    f"iFinD indicator probe succeeded: {indicator}; "
                    f"params={params!r}; functionpara={functionpara}"
                )
                return params, functionpara
            errors.append(error or "返回中无该指标数据")
            time.sleep(0.08)
    detail = "; ".join(errors[-4:])
    raise RuntimeError(
        f"iFinD指标 {indicator} 未找到可用参数格式（测试代码 {code}）：{detail}"
    )


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
            "net_subscription_1d_yi": round(sum(
                item.get("net_subscription_1d_yi", 0) for item in items
            ), 4),
            "net_subscription_5d_yi": round(sum(
                item.get("net_subscription_5d_yi", 0) for item in items
            ), 4),
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
            "net_subscription_1d_yi": round(sum(
                item.get("net_subscription_1d_yi", 0) for item in items
            ), 4),
            "net_subscription_5d_yi": round(sum(
                item.get("net_subscription_5d_yi", 0) for item in items
            ), 4),
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
            "net_subscription_1d_yi": round(sum(
                item.get("net_subscription_1d_yi", 0) for item in member_etfs
            ), 4),
            "net_subscription_5d_yi": round(sum(
                item.get("net_subscription_5d_yi", 0) for item in member_etfs
            ), 4),
            "exact_indices": [item["index_name"] for item in items],
            "review_required": any(item["theme_confidence"] != "高" for item in items),
            "flow_strength": 0,
        })
    themes.sort(key=lambda item: item["estimated_flow_yi"], reverse=True)
    max_theme_flow = max([abs(item["estimated_flow_yi"]) for item in themes] or [1])
    for item in themes:
        item["flow_strength"] = round(abs(item["estimated_flow_yi"]) / max_theme_flow * 100, 2)
    return indices, managers, themes


def update_history(
    metrics: dict[str, Any], observed_at: str, append_snapshot: bool = True
) -> tuple[list[dict[str, Any]], str]:
    history = load_json(HISTORY_FILE, [])
    if append_snapshot:
        history.append({"observed_at": observed_at, **metrics})
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=366)
    kept = []
    signature_fields = (
        "etf_count", "turnover_yi", "estimated_flow_yi", "inflow_count",
        "average_change_pct", "aum_yi",
    )
    prior_signature = None
    seen_times = set()
    for item in sorted(history, key=lambda value: value.get("observed_at", "")):
        try:
            observed = dt.datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
            if observed < cutoff or item["observed_at"] in seen_times:
                continue
            signature = tuple(item.get(field) for field in signature_fields)
            # A cached fallback can be written under a later workflow time. Exact
            # repetition across every market metric is treated as the same snapshot.
            if signature == prior_signature:
                continue
            kept.append(item)
            seen_times.add(item["observed_at"])
            prior_signature = signature
        except (KeyError, ValueError):
            continue
    maximum_coverage = max((int(item.get("etf_count") or 0) for item in kept), default=0)
    if maximum_coverage >= 500:
        minimum_coverage = maximum_coverage * 0.8
        kept = [
            item for item in kept
            if int(item.get("etf_count") or 0) >= minimum_coverage
        ]
    atomic_write(HISTORY_FILE, kept)
    history_start = kept[0]["observed_at"][:10] if kept else observed_at[:10]
    return kept, history_start


def main() -> None:
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    previous_payload = load_json(DASHBOARD_FILE, {})
    update_mode = os.environ.get("UPDATE_MODE", "full").strip().lower()
    if update_mode not in {"intraday", "daily", "full"}:
        raise RuntimeError(f"Unsupported UPDATE_MODE: {update_mode}")

    # The daily job must not stamp an evening workflow time onto intraday data.
    # It reuses the last market snapshot and only refreshes confirmed daily flows.
    if update_mode == "daily" and previous_payload.get("etfs"):
        rows = [dict(item) for item in previous_payload["etfs"]]
        public_quote_time = str(
            (((previous_payload.get("as_of") or {}).get("intraday_flow") or {}).get("time"))
            or previous_payload.get("generated_at")
            or generated_at
        )
        public_quote_fresh = False
    else:
        rows, public_quote_time, public_quote_fresh = fetch_universe(generated_at)
    source_parts = ["全量ETF列表/资金流估算"]
    if update_mode == "daily":
        source_parts.append("盘中行情沿用最近成功快照")
    elif not public_quote_fresh:
        source_parts.append("公开行情沿用最近成功快照")
    ifind_quote_fresh = False
    access_token = ""
    quote_message = "公开行情已刷新" if public_quote_fresh else "沿用最近一次成功行情"
    refresh_token = os.environ.get("IFIND_REFRESH_TOKEN", "").strip()
    if refresh_token:
        try:
            access_token = get_ifind_access_token(refresh_token)
            if update_mode in {"intraday", "full"} and update_ifind_quotes(rows, access_token):
                ifind_quote_fresh = True
                quote_message = "iFinD实时行情已刷新"
                source_parts.append("iFinD实时行情")
            elif update_mode == "daily":
                source_parts.append("iFinD日终任务")
            else:
                quote_message = (
                    "iFinD实时字段未返回可用值；"
                    + ("公开行情已刷新" if public_quote_fresh else "沿用最近成功行情")
                )
                source_parts.append("iFinD已连接，实时字段待授权")
        except Exception as exc:
            print(f"iFinD warning: {exc}", file=sys.stderr)
            quote_message = (
                f"iFinD连接失败；"
                + ("公开行情已刷新" if public_quote_fresh else "沿用最近成功行情")
            )
            source_parts.append("iFinD连接失败，保留公开行情")
    else:
        quote_message = (
            "未配置iFinD；"
            + ("公开行情已刷新" if public_quote_fresh else "沿用最近成功行情")
        )
        source_parts.append("未配置iFinD")

    rows = enrich_metadata(rows)
    subscription_market = previous_payload.get("daily_subscription_history") or []
    subscription_by_etf = previous_payload.get("daily_subscription_by_etf") or {}
    subscription_status = previous_payload.get("daily_subscription_status") or {
        "available": False,
        "message": "等待首次iFinD历史份额/净值回填",
    }
    daily_attempted = update_mode in {"daily", "full"}
    daily_succeeded = False
    if access_token and daily_attempted:
        try:
            subscription_market, subscription_by_etf, subscription_status = (
                fetch_ifind_subscription_history(rows, access_token, previous_payload)
            )
            subscription_status = {
                **subscription_status,
                "message": "iFinD日度净申赎更新成功",
                "last_attempt": generated_at,
                "last_success": generated_at,
            }
            daily_succeeded = True
            source_parts.append("iFinD日度净申赎")
        except Exception as exc:
            message = str(exc)[:300]
            print(f"iFinD daily subscription warning: {message}", file=sys.stderr)
            subscription_status = {
                **subscription_status,
                "available": bool(subscription_market),
                "message": message,
                "last_attempt": generated_at,
            }
            source_parts.append("iFinD日度净申赎待确认权限")
    elif daily_attempted:
        subscription_status = {
            **subscription_status,
            "available": bool(subscription_market),
            "message": "日终任务未配置iFinD Token，沿用已有历史数据",
            "last_attempt": generated_at,
        }
    else:
        source_parts.append("日度净申赎沿用最近日终数据")
    attach_subscription_metrics(rows, subscription_by_etf)
    indices, managers, themes = aggregate(rows)
    metrics = {
        "etf_count": len(rows),
        "turnover_yi": round(sum(row["turnover_yi"] for row in rows), 4),
        "estimated_flow_yi": round(sum(row["estimated_flow_yi"] for row in rows), 4),
        "inflow_count": sum(1 for row in rows if row["estimated_flow_yi"] > 0),
        "average_change_pct": round(sum(row["change_pct"] for row in rows) / max(1, len(rows)), 4),
        "aum_yi": round(sum(row["aum_yi"] for row in rows), 4),
    }
    market_quote_time = generated_at if ifind_quote_fresh else public_quote_time
    history, intraday_history_start = update_history(
        metrics, public_quote_time, append_snapshot=public_quote_fresh
    )
    subscription_latest_date = (
        subscription_market[-1]["date"] if subscription_market else ""
    )
    subscription_latest_time = (
        f"{subscription_latest_date}T15:00:00+08:00"
        if subscription_latest_date else generated_at
    )
    history_start = (
        subscription_market[0]["date"] if subscription_market else intraday_history_start
    )
    snapshot_basis = "盘中行情快照；非交易时段显示最近一次成功更新"
    previous_update_status = previous_payload.get("update_status") or {}
    daily_update_status = previous_update_status.get("daily") or {}
    if daily_attempted:
        daily_update_status = {
            "success": daily_succeeded,
            "attempted_at": generated_at,
            "data_time": subscription_latest_time if subscription_market else "",
            "message": subscription_status.get("message") or "日度任务已完成",
        }
    if daily_attempted:
        daily_asof_status = (
            "success" if daily_succeeded else ("cached" if subscription_market else "error")
        )
    else:
        daily_asof_status = (
            "success"
            if subscription_market and daily_update_status.get("success")
            else ("cached" if subscription_market else "error")
        )
    intraday_fresh = bool(public_quote_fresh or ifind_quote_fresh)
    intraday_status = {
        "success": intraday_fresh,
        "attempted_at": generated_at,
        "data_time": market_quote_time,
        "message": quote_message,
    }
    if update_mode == "daily" and previous_update_status.get("intraday"):
        intraday_status = previous_update_status["intraday"]
    payload = {
        "schema_version": 6,
        "generated_at": generated_at,
        "update_mode": update_mode,
        "update_status": {
            "last_job": {"mode": update_mode, "completed_at": generated_at},
            "intraday": intraday_status,
            "daily": daily_update_status,
        },
        "history_start": history_start,
        "source_label": " + ".join(source_parts),
        "as_of": {
            "turnover": {"time": market_quote_time, "basis": "当日累计成交额", "status": "success" if intraday_fresh else "cached"},
            "intraday_flow": {"time": market_quote_time, "basis": "成交方向资金流估算", "status": "success" if intraday_fresh else "cached"},
            "price_change": {"time": market_quote_time, "basis": "ETF行情等权涨跌幅", "status": "success" if intraday_fresh else "cached"},
            "aum": {"time": market_quote_time, "basis": "行情总市值口径，非确认净资产", "status": "success" if intraday_fresh else "cached"},
            "trend": {
                "time": subscription_latest_time,
                "basis": "iFinD ETF份额变化 × 当日单位净值",
                "status": daily_asof_status,
            },
            "daily_flow": {
                "time": subscription_latest_time,
                "basis": "日终净申购赎回；与盘中估算分开",
                "status": daily_asof_status,
            },
            "flow_structure": {"time": market_quote_time, "basis": snapshot_basis, "status": "success" if intraday_fresh else "cached"},
            "aggregation": {"time": market_quote_time, "basis": snapshot_basis, "status": "success" if intraday_fresh else "cached"},
            "managers": {"time": market_quote_time, "basis": snapshot_basis, "status": "success" if intraday_fresh else "cached"},
            "activity": {"time": market_quote_time, "basis": "当前资金流截面观察", "status": "success" if intraday_fresh else "cached"},
            "etf_detail": {"time": market_quote_time, "basis": snapshot_basis, "status": "success" if intraday_fresh else "cached"},
        },
        "metrics": metrics,
        "daily_subscription_history": subscription_market,
        "daily_subscription_by_etf": subscription_by_etf,
        "daily_subscription_status": subscription_status,
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
