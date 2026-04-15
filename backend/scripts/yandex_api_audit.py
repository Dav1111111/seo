"""
Yandex API Integration Audit Script.
Tests all Yandex services: OAuth token, Webmaster, Metrica.

Usage:
  cd backend
  python -m scripts.yandex_api_audit
"""

import asyncio
import json
import sys
import os
from datetime import date, timedelta
from urllib.parse import quote

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

# Load env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"))

OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN", "")
WEBMASTER_USER_ID = os.getenv("YANDEX_WEBMASTER_USER_ID", "")
WEBMASTER_HOST_ID = os.getenv("YANDEX_WEBMASTER_HOST_ID", "")
METRICA_COUNTER_ID = os.getenv("YANDEX_METRICA_COUNTER_ID", "")
CLIENT_ID = os.getenv("YANDEX_OAUTH_CLIENT_ID", "")

HEADERS = {
    "Authorization": f"OAuth {OAUTH_TOKEN}",
    "Accept": "application/json",
}

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"
INFO = "\033[94mℹ\033[0m"


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def test_oauth_token(client: httpx.AsyncClient) -> bool:
    """Test 1: Validate OAuth token by checking user info."""
    print_section("1. OAuth Token Validation")
    print(f"  Token prefix: {OAUTH_TOKEN[:10]}...")
    print(f"  Client ID: {CLIENT_ID}")

    try:
        resp = await client.get(
            "https://login.yandex.ru/info",
            headers=HEADERS,
            params={"format": "json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  {PASS} Token valid!")
            print(f"  {INFO} User: {data.get('display_name', 'N/A')}")
            print(f"  {INFO} Login: {data.get('login', 'N/A')}")
            print(f"  {INFO} UID: {data.get('id', 'N/A')}")
            print(f"  {INFO} Email: {data.get('default_email', 'N/A')}")
            return True
        else:
            print(f"  {FAIL} Token invalid! Status: {resp.status_code}")
            print(f"  Response: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False


async def test_webmaster_user(client: httpx.AsyncClient) -> bool:
    """Test 2: Verify Webmaster user_id and list all hosts."""
    print_section("2. Webmaster — User & Hosts")
    print(f"  Configured user_id: {WEBMASTER_USER_ID}")
    print(f"  Configured host_id: {WEBMASTER_HOST_ID}")

    # First, get the correct user_id
    try:
        resp = await client.get(
            "https://api.webmaster.yandex.net/v4/user",
            headers=HEADERS,
        )
        if resp.status_code == 200:
            data = resp.json()
            actual_user_id = str(data.get("user_id", ""))
            print(f"  {PASS} API accessible!")
            print(f"  {INFO} Actual user_id from API: {actual_user_id}")

            if actual_user_id != WEBMASTER_USER_ID:
                print(f"  {FAIL} MISMATCH! .env has '{WEBMASTER_USER_ID}', API returns '{actual_user_id}'")
            else:
                print(f"  {PASS} user_id matches!")
        else:
            print(f"  {FAIL} Cannot get user info: {resp.status_code}")
            print(f"  Response: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False

    # List all hosts
    print(f"\n  --- Listing all hosts for user {actual_user_id} ---")
    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{actual_user_id}/hosts",
            headers=HEADERS,
        )
        if resp.status_code == 200:
            data = resp.json()
            hosts = data.get("hosts", [])
            print(f"  {PASS} Found {len(hosts)} host(s):")
            for h in hosts:
                host_id = h.get("host_id", "")
                ascii_url = h.get("ascii_host_url", "")
                unicode_url = h.get("unicode_host_url", "")
                verified = h.get("verified", False)
                main_mirror = h.get("main_mirror", "")
                print(f"\n    Host ID: {host_id}")
                print(f"    ASCII URL: {ascii_url}")
                print(f"    Unicode URL: {unicode_url}")
                print(f"    Verified: {verified}")
                if main_mirror:
                    print(f"    Main mirror: {main_mirror}")

                if host_id == WEBMASTER_HOST_ID:
                    print(f"    {PASS} << THIS matches YANDEX_WEBMASTER_HOST_ID in .env")
                else:
                    print(f"    {WARN} Does NOT match .env host_id")

            # Check if configured host_id exists in the list
            host_ids = [h.get("host_id") for h in hosts]
            if WEBMASTER_HOST_ID and WEBMASTER_HOST_ID not in host_ids:
                print(f"\n  {FAIL} CRITICAL: Configured host_id '{WEBMASTER_HOST_ID}' NOT found in Webmaster!")
                print(f"  Available host_ids: {host_ids}")

            return True
        else:
            print(f"  {FAIL} Cannot list hosts: {resp.status_code}")
            print(f"  Response: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False


async def test_webmaster_queries(client: httpx.AsyncClient, user_id: str, host_id: str, host_label: str) -> bool:
    """Test 3: Fetch popular queries for a host."""
    print(f"\n  --- Popular queries for {host_label} ---")

    encoded_host = quote(host_id, safe="")
    end_date = date.today() - timedelta(days=5)
    start_date = end_date - timedelta(days=7)

    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{encoded_host}/search-queries/popular",
            headers=HEADERS,
            params=[
                ("order_by", "TOTAL_SHOWS"),
                ("date_from", start_date.isoformat()),
                ("date_to", end_date.isoformat()),
                ("query_indicator", "TOTAL_SHOWS"),
                ("query_indicator", "TOTAL_CLICKS"),
                ("query_indicator", "AVG_SHOW_POSITION"),
                ("limit", "10"),
            ],
        )
        if resp.status_code == 200:
            data = resp.json()
            queries = data.get("queries", [])
            print(f"    {PASS} Got {len(queries)} queries (period: {start_date} → {end_date})")
            for q in queries[:5]:
                text = q.get("query_text", "?")
                indicators = q.get("indicators", {})
                shows = indicators.get("TOTAL_SHOWS", 0)
                clicks = indicators.get("TOTAL_CLICKS", 0)
                pos = indicators.get("AVG_SHOW_POSITION", 0)
                # Handle both aggregated numbers and daily arrays
                if isinstance(shows, list):
                    shows_total = sum(d.get("value", 0) for d in shows)
                    clicks_total = sum(d.get("value", 0) for d in (clicks if isinstance(clicks, list) else []))
                    print(f"      '{text}': shows={shows_total}, clicks={clicks_total} (daily breakdown)")
                else:
                    print(f"      '{text}': shows={shows}, clicks={clicks}, pos={pos}")
            return True
        else:
            print(f"    {FAIL} Status {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"    {FAIL} Request failed: {e}")
        return False


async def test_webmaster_indexing(client: httpx.AsyncClient, user_id: str, host_id: str, host_label: str) -> bool:
    """Test 4: Fetch indexing history."""
    print(f"\n  --- Indexing history for {host_label} ---")

    encoded_host = quote(host_id, safe="")
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=14)

    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{encoded_host}/indexing/history",
            headers=HEADERS,
            params={
                "date_from": start_date.isoformat(),
                "date_to": end_date.isoformat(),
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"    {PASS} Indexing data received!")
            print(f"    Response keys: {list(data.keys())}")
            for key in ["HTTP_2XX", "HTTP_3XX", "HTTP_4XX", "HTTP_5XX"]:
                series = data.get(key, [])
                if series:
                    latest = series[-1] if series else {}
                    print(f"      {key}: {len(series)} data points, latest: {latest}")
                else:
                    print(f"      {key}: no data")
            return True
        else:
            print(f"    {FAIL} Status {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"    {FAIL} Request failed: {e}")
        return False


async def test_webmaster_search_events(client: httpx.AsyncClient, user_id: str, host_id: str, host_label: str) -> bool:
    """Test 5: Fetch search events."""
    print(f"\n  --- Search events for {host_label} ---")

    encoded_host = quote(host_id, safe="")
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=14)

    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{encoded_host}/search-urls/events/history",
            headers=HEADERS,
            params={
                "date_from": start_date.isoformat(),
                "date_to": end_date.isoformat(),
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"    {PASS} Search events received!")
            print(f"    Response keys: {list(data.keys())}")
            for key in ["APPEARED_IN_SEARCH", "REMOVED_FROM_SEARCH"]:
                series = data.get(key, [])
                if series:
                    print(f"      {key}: {len(series)} data points, latest: {series[-1] if series else 'none'}")
                else:
                    print(f"      {key}: no data")
            return True
        else:
            print(f"    {FAIL} Status {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"    {FAIL} Request failed: {e}")
        return False


async def test_webmaster_sitemaps(client: httpx.AsyncClient, user_id: str, host_id: str, host_label: str) -> bool:
    """Test 6: Fetch sitemaps."""
    print(f"\n  --- Sitemaps for {host_label} ---")

    encoded_host = quote(host_id, safe="")

    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{encoded_host}/sitemaps/",
            headers=HEADERS,
        )
        if resp.status_code == 200:
            data = resp.json()
            sitemaps = data.get("sitemaps", [])
            print(f"    {PASS} Found {len(sitemaps)} sitemap(s)")
            for sm in sitemaps:
                print(f"      URL: {sm.get('sitemap_url', 'N/A')}")
                print(f"      Type: {sm.get('type', 'N/A')}, URLs: {sm.get('urls_count', 'N/A')}")
            return True
        else:
            print(f"    {FAIL} Status {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"    {FAIL} Request failed: {e}")
        return False


async def test_webmaster_host_info(client: httpx.AsyncClient, user_id: str, host_id: str, host_label: str) -> bool:
    """Test: Get detailed host info."""
    print(f"\n  --- Host summary for {host_label} ---")

    encoded_host = quote(host_id, safe="")

    try:
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{encoded_host}/summary",
            headers=HEADERS,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"    {PASS} Host summary received!")
            for key, val in data.items():
                print(f"      {key}: {val}")
            return True
        else:
            print(f"    {WARN} Status {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"    {FAIL} Request failed: {e}")
        return False


async def test_metrica(client: httpx.AsyncClient) -> bool:
    """Test 7: Yandex Metrica."""
    print_section("4. Yandex Metrica")
    print(f"  Configured counter_id: '{METRICA_COUNTER_ID}'")

    if not METRICA_COUNTER_ID:
        print(f"  {WARN} YANDEX_METRICA_COUNTER_ID is empty — Metrica collection disabled!")

        # Try to list counters available to this token
        print(f"\n  Trying to list available counters...")
        try:
            resp = await client.get(
                "https://api-metrika.yandex.net/management/v1/counters",
                headers=HEADERS,
            )
            if resp.status_code == 200:
                data = resp.json()
                counters = data.get("counters", [])
                print(f"  {PASS} Found {len(counters)} counter(s) accessible with this token:")
                for c in counters:
                    print(f"    ID: {c.get('id')} | Name: {c.get('name', 'N/A')} | Site: {c.get('site', 'N/A')} | Status: {c.get('status', 'N/A')}")
            elif resp.status_code == 403:
                print(f"  {WARN} No Metrica access with this token (403)")
                print(f"  Response: {resp.text[:200]}")
            else:
                print(f"  {FAIL} Status {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  {FAIL} Request failed: {e}")
        return False

    # Test with counter_id
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=7)

    try:
        resp = await client.get(
            "https://api-metrika.yandex.net/stat/v1/data/bytime",
            headers=HEADERS,
            params={
                "id": METRICA_COUNTER_ID,
                "metrics": "ym:s:visits,ym:s:pageviews",
                "date1": start_date.isoformat(),
                "date2": end_date.isoformat(),
                "group": "day",
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  {PASS} Metrica data received!")
            total_rows = data.get("total_rows", 0)
            print(f"  Total rows: {total_rows}")
            return True
        else:
            print(f"  {FAIL} Status {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False


async def test_direct(client: httpx.AsyncClient) -> bool:
    """Test 8: Yandex Direct API (basic check)."""
    print_section("5. Yandex Direct API")

    try:
        resp = await client.post(
            "https://api.direct.yandex.com/json/v5/campaigns",
            headers={
                "Authorization": f"Bearer {OAUTH_TOKEN}",
                "Accept-Language": "ru",
            },
            json={
                "method": "get",
                "params": {
                    "SelectionCriteria": {},
                    "FieldNames": ["Id", "Name", "Status"],
                    "Page": {"Limit": 5},
                },
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if "result" in data:
                campaigns = data["result"].get("Campaigns", [])
                print(f"  {PASS} Direct API accessible! Found {len(campaigns)} campaign(s)")
                for c in campaigns[:3]:
                    print(f"    ID: {c.get('Id')} | Name: {c.get('Name')} | Status: {c.get('Status')}")
            elif "error" in data:
                err = data["error"]
                print(f"  {WARN} Direct API error: [{err.get('error_code')}] {err.get('error_string')}: {err.get('error_detail', '')}")
            return True
        else:
            print(f"  {FAIL} Status {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False


async def test_wordstat(client: httpx.AsyncClient) -> bool:
    """Test 9: Wordstat API (via Direct)."""
    print_section("6. Wordstat API")

    try:
        resp = await client.post(
            "https://api.direct.yandex.com/v4/json/",
            headers={
                "Authorization": f"Bearer {OAUTH_TOKEN}",
                "Accept-Language": "ru",
            },
            json={
                "method": "CreateNewWordstatReport",
                "param": {
                    "Phrases": ["туры в сочи"],
                    "GeoID": [225],  # Russia
                },
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if "data" in data:
                print(f"  {PASS} Wordstat API accessible! Report ID: {data['data']}")
            elif "error_code" in data:
                print(f"  {WARN} Wordstat error: [{data.get('error_code')}] {data.get('error_str')}: {data.get('error_detail', '')}")
            else:
                print(f"  {INFO} Response: {json.dumps(data, ensure_ascii=False)[:300]}")
            return True
        else:
            print(f"  {FAIL} Status {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  {FAIL} Request failed: {e}")
        return False


async def main():
    print("\n" + "="*60)
    print("  YANDEX API INTEGRATION AUDIT")
    print(f"  Date: {date.today()}")
    print("="*60)

    # Show configuration
    print_section("0. Configuration from .env")
    print(f"  YANDEX_OAUTH_TOKEN: {OAUTH_TOKEN[:10]}...{OAUTH_TOKEN[-5:]}" if OAUTH_TOKEN else f"  {FAIL} YANDEX_OAUTH_TOKEN: EMPTY!")
    print(f"  YANDEX_WEBMASTER_USER_ID: {WEBMASTER_USER_ID}" if WEBMASTER_USER_ID else f"  {FAIL} YANDEX_WEBMASTER_USER_ID: EMPTY!")
    print(f"  YANDEX_WEBMASTER_HOST_ID: {WEBMASTER_HOST_ID}" if WEBMASTER_HOST_ID else f"  {FAIL} YANDEX_WEBMASTER_HOST_ID: EMPTY!")
    print(f"  YANDEX_METRICA_COUNTER_ID: {METRICA_COUNTER_ID}" if METRICA_COUNTER_ID else f"  {WARN} YANDEX_METRICA_COUNTER_ID: EMPTY!")
    print(f"  YANDEX_OAUTH_CLIENT_ID: {CLIENT_ID}" if CLIENT_ID else f"  {WARN} YANDEX_OAUTH_CLIENT_ID: EMPTY!")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. OAuth
        token_ok = await test_oauth_token(client)
        if not token_ok:
            print(f"\n{FAIL} OAuth token is invalid — cannot proceed with other tests.")
            return

        # 2. Webmaster — user + hosts discovery
        print_section("2. Webmaster — User & Host Discovery")

        # Get actual user_id
        resp = await client.get(
            "https://api.webmaster.yandex.net/v4/user",
            headers=HEADERS,
        )
        user_data = resp.json()
        actual_user_id = str(user_data.get("user_id", WEBMASTER_USER_ID))

        if actual_user_id != WEBMASTER_USER_ID:
            print(f"  {FAIL} YANDEX_WEBMASTER_USER_ID mismatch!")
            print(f"       .env:  {WEBMASTER_USER_ID}")
            print(f"       API:   {actual_user_id}")
            print(f"       ACTION: Update .env with correct user_id")
        else:
            print(f"  {PASS} user_id OK: {actual_user_id}")

        # List hosts
        resp = await client.get(
            f"https://api.webmaster.yandex.net/v4/user/{actual_user_id}/hosts",
            headers=HEADERS,
        )
        hosts_data = resp.json()
        all_hosts = hosts_data.get("hosts", [])

        print(f"\n  Found {len(all_hosts)} host(s) in Webmaster:")
        discovered_hosts = []
        for h in all_hosts:
            host_id = h.get("host_id", "")
            ascii_url = h.get("ascii_host_url", "")
            unicode_url = h.get("unicode_host_url", "")
            verified = h.get("verified", False)
            main_mirror = h.get("main_mirror", "")

            discovered_hosts.append({
                "host_id": host_id,
                "ascii_url": ascii_url,
                "unicode_url": unicode_url,
                "verified": verified,
                "main_mirror": main_mirror,
            })

            match_marker = f" {PASS} << matches .env" if host_id == WEBMASTER_HOST_ID else ""
            print(f"\n    host_id: {host_id}{match_marker}")
            print(f"    ASCII:   {ascii_url}")
            print(f"    Unicode: {unicode_url}")
            print(f"    Verified: {verified}")
            if main_mirror:
                print(f"    Main mirror: {main_mirror}")

        configured_found = any(h["host_id"] == WEBMASTER_HOST_ID for h in discovered_hosts)
        if not configured_found and WEBMASTER_HOST_ID:
            print(f"\n  {FAIL} CRITICAL: .env host_id '{WEBMASTER_HOST_ID}' NOT found in Webmaster!")
            print(f"  This explains why data collection fails!")

        # 3. Test each discovered host
        print_section("3. Webmaster — Per-Host API Tests")
        for h in discovered_hosts:
            host_id = h["host_id"]
            label = h.get("unicode_url") or h.get("ascii_url") or host_id

            print(f"\n{'─'*50}")
            print(f"  Testing: {label}")
            print(f"  host_id: {host_id}")
            print(f"{'─'*50}")

            await test_webmaster_host_info(client, actual_user_id, host_id, label)
            await test_webmaster_queries(client, actual_user_id, host_id, label)
            await test_webmaster_indexing(client, actual_user_id, host_id, label)
            await test_webmaster_search_events(client, actual_user_id, host_id, label)
            await test_webmaster_sitemaps(client, actual_user_id, host_id, label)

        # 4. Metrica
        await test_metrica(client)

        # 5. Direct
        await test_direct(client)

        # 6. Wordstat
        await test_wordstat(client)

        # Summary
        print_section("AUDIT SUMMARY")
        print(f"  OAuth: {'OK' if token_ok else 'FAILED'}")
        print(f"  Webmaster user_id: {'OK' if actual_user_id == WEBMASTER_USER_ID else 'MISMATCH — needs fix'}")
        print(f"  Webmaster hosts found: {len(discovered_hosts)}")
        print(f"  Configured host_id in .env: {'Found' if configured_found else 'NOT FOUND — needs fix'}")
        print(f"  Metrica counter: {'Configured' if METRICA_COUNTER_ID else 'NOT configured — collection disabled'}")

        if discovered_hosts:
            print(f"\n  RECOMMENDED .env values for multi-site:")
            for h in discovered_hosts:
                print(f"    # {h.get('unicode_url', h['host_id'])}")
                print(f"    # host_id: {h['host_id']}")


if __name__ == "__main__":
    asyncio.run(main())
