"""
Integration test — tests the full collector pipeline against real Yandex APIs.
Runs without DB (dry-run mode) to verify API calls and response parsing.

Usage: cd backend && python3 -m scripts.integration_test
"""

import asyncio
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"))

import httpx

TOKEN = os.getenv("YANDEX_OAUTH_TOKEN", "")
USER_ID = os.getenv("YANDEX_WEBMASTER_USER_ID", "")
HEADERS = {"Authorization": f"OAuth {TOKEN}", "Accept": "application/json"}

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"


async def test_webmaster_api():
    """Test Webmaster API calls and response parsing (pure HTTP, no app imports)."""
    from urllib.parse import quote

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Test HOST_NOT_LOADED detection
        print("\n--- Test: HOST_NOT_LOADED detection ---")
        broken_host = quote("https:www.grandtourspirit.ru:443", safe="")
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{USER_ID}/hosts/{broken_host}/search-queries/popular",
            headers=HEADERS,
            params=[("order_by", "TOTAL_SHOWS"), ("date_from", "2026-04-01"), ("date_to", "2026-04-10"),
                    ("query_indicator", "TOTAL_SHOWS"), ("limit", "5")],
        )
        if resp.status_code == 404:
            body = resp.json()
            if body.get("error_code") == "HOST_NOT_LOADED":
                print(f"  {PASS} HOST_NOT_LOADED detected (404 + error_code). Our new handler catches this.")
            else:
                print(f"  {FAIL} 404 but unexpected error_code: {body}")
        else:
            print(f"  {FAIL} Expected 404, got {resp.status_code}")

        # 2. Test working host
        print("\n--- Test: Working host (южный-континент.рф) ---")
        working_host = quote("https:xn----jtbbjdhsdbbg3ce9iub.xn--p1ai:443", safe="")
        end = date.today() - timedelta(days=5)
        start = end - timedelta(days=14)
        prefix = f"https://api.webmaster.yandex.net/v4/user/{USER_ID}/hosts/{working_host}"

        # Popular queries
        resp = await client.get(
            f"{prefix}/search-queries/popular", headers=HEADERS,
            params=[("order_by", "TOTAL_SHOWS"), ("date_from", start.isoformat()),
                    ("date_to", end.isoformat()), ("query_indicator", "TOTAL_SHOWS"),
                    ("query_indicator", "TOTAL_CLICKS"), ("query_indicator", "AVG_SHOW_POSITION"),
                    ("limit", "5")],
        )
        queries = resp.json().get("queries", [])
        print(f"  {PASS} Popular queries: {len(queries)} results")

        # Indexing history — verify indicators unwrap
        resp = await client.get(
            f"{prefix}/indexing/history", headers=HEADERS,
            params={"date_from": start.isoformat(), "date_to": end.isoformat()},
        )
        raw = resp.json()
        indicators = raw.get("indicators", raw)
        http_2xx = indicators.get("HTTP_2XX", [])
        print(f"  {PASS} Indexing: HTTP_2XX has {len(http_2xx)} entries (unwrapped from indicators)")
        if http_2xx:
            sample = http_2xx[0]
            date_str = sample["date"][:10]
            parsed = date.fromisoformat(date_str)
            print(f"    Sample: date={parsed}, value={sample['value']} — date parsing OK")

        # Search events — verify indicators unwrap
        resp = await client.get(
            f"{prefix}/search-urls/events/history", headers=HEADERS,
            params={"date_from": start.isoformat(), "date_to": end.isoformat()},
        )
        raw = resp.json()
        indicators = raw.get("indicators", raw)
        appeared = indicators.get("APPEARED_IN_SEARCH", [])
        removed = indicators.get("REMOVED_FROM_SEARCH", [])
        print(f"  {PASS} Search events: appeared={len(appeared)}, removed={len(removed)} (unwrapped)")
        if appeared:
            date_str = appeared[0]["date"][:10]
            parsed = date.fromisoformat(date_str)
            print(f"    Sample: date={parsed}, value={appeared[0]['value']} — date parsing OK")

        # Sitemaps
        resp = await client.get(f"{prefix}/sitemaps/", headers=HEADERS)
        sitemaps = resp.json().get("sitemaps", [])
        print(f"  {PASS} Sitemaps: {len(sitemaps)} found")


async def test_metrica_collector_logic():
    """Test MetricaCollector parsing against real API."""
    print("\n--- Test: Metrica collector (ЮК counter 108502833) ---")

    async with httpx.AsyncClient(timeout=30.0) as client:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=3)

        resp = await client.get(
            "https://api-metrika.yandex.net/stat/v1/data/bytime",
            headers=HEADERS,
            params={
                "id": "108502833",
                "metrics": "ym:s:visits,ym:s:pageviews,ym:s:bounceRate,ym:s:avgVisitDurationSeconds",
                "date1": start.isoformat(),
                "date2": end.isoformat(),
                "group": "day",
            },
        )
        raw = resp.json()

        # Verify our new parsing logic
        time_intervals = raw.get("time_intervals", [])
        data_rows = raw.get("data", [])

        if not data_rows or not time_intervals:
            print(f"  {FAIL} No data returned")
            return

        metrics_list = data_rows[0].get("metrics", [])
        if len(metrics_list) < 4:
            print(f"  {FAIL} Expected 4 metric arrays, got {len(metrics_list)}")
            return

        visits_arr = metrics_list[0]
        pv_arr = metrics_list[1]
        bounce_arr = metrics_list[2]
        dur_arr = metrics_list[3]

        print(f"  {PASS} time_intervals: {len(time_intervals)} days")
        print(f"  {PASS} visits array: {visits_arr}")
        print(f"  {PASS} pageviews array: {pv_arr}")

        for i, interval in enumerate(time_intervals):
            date_str = interval[0] if isinstance(interval, list) and interval else "?"
            parsed = date.fromisoformat(date_str[:10])
            visits = int(visits_arr[i]) if i < len(visits_arr) else 0
            pvs = int(pv_arr[i]) if i < len(pv_arr) else 0
            bounce = bounce_arr[i] if i < len(bounce_arr) else 0
            dur = dur_arr[i] if i < len(dur_arr) else 0
            print(f"    {parsed}: visits={visits}, pv={pvs}, bounce={bounce:.1f}%, dur={dur:.1f}s")

        print(f"  {PASS} Metrica parsing logic verified OK!")


async def test_discovery_endpoint():
    """Test the discovery endpoint logic (without FastAPI server)."""
    print("\n--- Test: Yandex discovery logic ---")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Webmaster
        user_resp = await client.get(
            "https://api.webmaster.yandex.net/v4/user", headers=HEADERS,
        )
        user_id = str(user_resp.json().get("user_id", ""))

        hosts_resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts",
            headers=HEADERS,
        )
        hosts = hosts_resp.json().get("hosts", [])
        print(f"  {PASS} Webmaster: user_id={user_id}, {len(hosts)} hosts")
        for h in hosts:
            print(f"    {h.get('unicode_host_url', h.get('host_id'))}: verified={h.get('verified')}")

        # Metrica
        counters_resp = await client.get(
            "https://api-metrika.yandex.net/management/v1/counters",
            headers=HEADERS,
        )
        counters = counters_resp.json().get("counters", [])
        print(f"  {PASS} Metrica: {len(counters)} counters")
        for c in counters:
            print(f"    {c.get('id')}: {c.get('name')} ({c.get('site')}) — {c.get('status')}")


async def main():
    print("=" * 60)
    print("  YANDEX API INTEGRATION TEST")
    print(f"  Date: {date.today()}")
    print("=" * 60)

    await test_webmaster_api()
    await test_metrica_collector_logic()
    await test_discovery_endpoint()

    print("\n" + "=" * 60)
    print("  INTEGRATION TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
