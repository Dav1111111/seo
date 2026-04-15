"""Quick check of exact Yandex API response structures."""
import asyncio, json, os, sys
from datetime import date, timedelta
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", ".env"))
import httpx

TOKEN = os.getenv("YANDEX_OAUTH_TOKEN", "")
USER_ID = os.getenv("YANDEX_WEBMASTER_USER_ID", "")
# Use the working host (южный-континент.рф)
HOST_ID = "https:xn----jtbbjdhsdbbg3ce9iub.xn--p1ai:443"
HEADERS = {"Authorization": f"OAuth {TOKEN}", "Accept": "application/json"}

async def main():
    encoded = quote(HOST_ID, safe="")
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=14)

    async with httpx.AsyncClient(timeout=30.0) as c:
        # 1. Indexing history
        print("=== INDEXING HISTORY RAW RESPONSE ===")
        r = await c.get(
            f"https://api.webmaster.yandex.net/v4/user/{USER_ID}/hosts/{encoded}/indexing/history",
            headers=HEADERS,
            params={"date_from": start.isoformat(), "date_to": end.isoformat()},
        )
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))

        # 2. Search events
        print("\n=== SEARCH EVENTS RAW RESPONSE ===")
        r = await c.get(
            f"https://api.webmaster.yandex.net/v4/user/{USER_ID}/hosts/{encoded}/search-urls/events/history",
            headers=HEADERS,
            params={"date_from": start.isoformat(), "date_to": end.isoformat()},
        )
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))

        # 3. Popular queries
        print("\n=== POPULAR QUERIES RAW RESPONSE ===")
        r = await c.get(
            f"https://api.webmaster.yandex.net/v4/user/{USER_ID}/hosts/{encoded}/search-queries/popular",
            headers=HEADERS,
            params=[
                ("order_by", "TOTAL_SHOWS"),
                ("date_from", start.isoformat()),
                ("date_to", end.isoformat()),
                ("query_indicator", "TOTAL_SHOWS"),
                ("query_indicator", "TOTAL_CLICKS"),
                ("query_indicator", "AVG_SHOW_POSITION"),
                ("limit", "5"),
            ],
        )
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))

asyncio.run(main())
