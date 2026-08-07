#!/usr/bin/env python3
"""One-shot iFinD response-shape diagnostic. Never prints credentials."""

import json
import os

from update_data import IFIND_BASE, SESSION, get_ifind_access_token


def main() -> None:
    refresh_token = os.environ.get("IFIND_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("IFIND_REFRESH_TOKEN is not configured")
    access_token = get_ifind_access_token(refresh_token)
    response = SESSION.post(
        f"{IFIND_BASE}/real_time_quotation",
        headers={"access_token": access_token, "Content-Type": "application/json"},
        json={
            "codes": "510300.SH",
            "indicators": "latest,changeRatio,amount",
        },
        timeout=(15, 45),
    )
    print(f"HTTP status: {response.status_code}")
    response.raise_for_status()
    payload = response.json()
    print("Top-level keys:", sorted(payload.keys()))
    print("Sanitized response (market data only):")
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:12000])


if __name__ == "__main__":
    main()
