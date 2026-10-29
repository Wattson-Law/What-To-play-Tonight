"""Network diagnostics for Steam endpoints."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
PATHS = ("api/appdetails?appids=548430", "search/results/?query=&start=0&count=10", "appreviews/548430?json=1&language=schinese&filter=all&purchase_type=all")


def build_url(host: str, path: str) -> str:
    """Join a host and endpoint path without duplicating slashes."""
    return host.rstrip("/") + "/" + path.lstrip("/")


def classify_status(status_code: int | None) -> str:
    if status_code in REDIRECT_STATUSES:
        return "redirect_trap"
    if status_code is None:
        return "error"
    return "ok" if 200 <= status_code < 400 else "http_error"


def probe_endpoint(client: httpx.Client, url: str) -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {"url": url}
    try:
        response = client.get(url)
        result.update(
            status=response.status_code,
            redirect_location=response.headers.get("location"),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            classification=classify_status(response.status_code),
            error=None,
        )
    except Exception as exc:  # diagnostics should continue across endpoints
        result.update(
            status=None,
            redirect_location=None,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
            classification="error",
            error=f"{type(exc).__name__}: {exc}",
        )
    return result


def combination_failed(record: dict[str, Any]) -> bool:
    return all(not (isinstance(item.get("status"), int) and 200 <= item["status"] < 300) for item in record["probes"])


def build_guidance(records: list[dict[str, Any]]) -> list[str]:
    """Return actionable proxy and fallback guidance for failed review probes."""
    proxy_review_failed = any(
        record.get("combination", "").startswith(("C", "D"))
        and any(
            "/appreviews/" in item.get("url", "")
            and not (isinstance(item.get("status"), int) and 200 <= item["status"] < 300)
            for item in record.get("probes", [])
        )
        for record in records
    )
    if not proxy_review_failed:
        return []
    return [
        "Clash guidance: add these highest-priority rules: DOMAIN-SUFFIX,steampowered.com,PROXY and DOMAIN-SUFFIX,steamcommunity.com,PROXY.",
        "Fallback Mode: use steamstore-a.akamaihd.net/app/<id>/?l=schinese&cc=cn for tags and nine-tier summaries; leave appreviews to the overseas GitHub Actions snapshot job.",
    ]


def run_combination(name: str, host: str, proxy: str | None) -> dict[str, Any]:
    transport = None
    results = []
    with httpx.Client(proxy=proxy, follow_redirects=False, timeout=15.0, headers={"User-Agent": USER_AGENT}, transport=transport) as client:
        for path in PATHS:
            results.append(probe_endpoint(client, build_url(host, path)))
    return {"combination": name, "proxy": proxy, "host": host, "probes": results}


def format_table(records: list[dict[str, Any]]) -> str:
    lines = ["Combination | Endpoint | Status | Redirect | Elapsed ms | Result", "-" * 86]
    for record in records:
        for item in record["probes"]:
            endpoint = item["url"].split("/", 3)[-1]
            lines.append(f"{record['combination']} | {endpoint} | {item.get('status') or '-'} | {item.get('redirect_location') or '-'} | {item.get('elapsed_ms', '-') } | {item['classification']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Steam hosts and endpoint behavior")
    parser.add_argument("--proxy", nargs="?", const="__default__", help="also run proxy probes; optional URL")
    parser.add_argument("--strict", action="store_true", help="exit nonzero when every probe in a combination fails")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit JSON only")
    args = parser.parse_args(argv)
    proxy = os.environ.get("STEAM_PROXY", "http://127.0.0.1:7890") if args.proxy in (None, "__default__") else args.proxy
    records = [
        run_combination("A direct + official", "https://store.steampowered.com", None),
        run_combination("B direct + CDN", "https://steamstore-a.akamaihd.net", None),
        run_combination("C proxy + official", "https://store.steampowered.com", proxy),
        run_combination("D proxy + CDN", "https://steamstore-a.akamaihd.net", proxy),
    ]
    guidance = build_guidance(records)
    if args.as_json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        for message in guidance:
            print(message, file=sys.stderr)
    else:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        print(format_table(records))
        if guidance:
            print("\n".join(guidance))
    if args.strict and any(combination_failed(r) for r in records):
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

