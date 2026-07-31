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

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MASTER_FILE = DATA_DIR / "master.json"
DASHBOARD_FILE = DATA_DIR / "dashboard.json"
HISTORY_FILE = DATA_DIR / "market_history.json"

IFIND_BASE = "https://quantapi.51ifind.com/api/v1"
EASTMONEY_LIST = "https://88.push2.eastmoney.com/api/qt/clist/get"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0 ETF-Flow-Dashboard/1.0"})


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
    params = {
        "pn": 1,
        "pz": 5000,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f6",
        "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024",
        "fields": "f12,f14,f2,f3,f6,f20,f62,f184",
    }
    response = SESSION.get(EASTMONEY_LIST, params=params, timeout=30)
    response.raise_for_status()
    diff = (((response.json() or {}).get("data") or {}).get("diff") or [])
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


def aggregate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    return indices, managers


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
    indices, managers = aggregate(rows)
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
        "managers": managers,
        "etfs": rows,
    }
    atomic_write(DASHBOARD_FILE, payload)
    print(f"Updated {len(rows)} ETFs, {len(indices)} index groups, {len(managers)} managers")


if __name__ == "__main__":
    main()
