"""
Base collector with retry, rate limiting, and error handling.
All Yandex API collectors inherit from this.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Yandex API allows ~5 req/sec; we stay under 3 for safety
_semaphore = asyncio.Semaphore(3)


class CollectorError(Exception):
    pass


class HostNotLoadedError(CollectorError):
    """Yandex Webmaster returns this when a host is verified but data isn't loaded yet."""
    pass


class RateLimitError(CollectorError):
    pass


class BaseCollector:
    base_url: str = ""
    max_retries: int = 3
    backoff_base: float = 1.0  # seconds

    def __init__(self, oauth_token: str):
        self.oauth_token = oauth_token
        self._client: httpx.AsyncClient | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"OAuth {self.oauth_token}",
            "Accept": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=30.0,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        params: Any = None,
        json: dict[str, Any] | None = None,
    ) -> dict:
        """HTTP request with semaphore rate limiting + exponential backoff retry.

        params can be dict or list[tuple] (for repeated keys like query_indicator).
        """
        client = await self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            async with _semaphore:
                try:
                    resp = await client.request(method, path, params=params, json=json)

                    if resp.status_code == 429:
                        wait = self.backoff_base * (2 ** attempt)
                        logger.warning("Rate limited on %s, waiting %.1fs", path, wait)
                        await asyncio.sleep(wait)
                        continue

                    if resp.status_code >= 500:
                        wait = self.backoff_base * (2 ** attempt)
                        logger.warning("Server error %d on %s, retry in %.1fs", resp.status_code, path, wait)
                        await asyncio.sleep(wait)
                        continue

                    # Detect Yandex Webmaster HOST_NOT_LOADED (404 with specific error_code)
                    if resp.status_code == 404:
                        try:
                            body = resp.json()
                            if body.get("error_code") == "HOST_NOT_LOADED":
                                raise HostNotLoadedError(
                                    f"Host not loaded in Yandex Webmaster: {body.get('error_message', '')}"
                                )
                        except (ValueError, KeyError):
                            pass

                    resp.raise_for_status()
                    return resp.json()

                except HostNotLoadedError:
                    raise  # don't retry, propagate immediately

                except httpx.TimeoutException as exc:
                    last_error = exc
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning("Timeout on %s (attempt %d), retry in %.1fs", path, attempt + 1, wait)
                    await asyncio.sleep(wait)

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (401, 403):
                        raise CollectorError(f"Auth error {exc.response.status_code}: check OAuth token") from exc
                    last_error = exc
                    logger.error("HTTP %d on %s: %s", exc.response.status_code, path, exc.response.text[:200])
                    raise CollectorError(str(exc)) from exc

        raise CollectorError(f"Failed after {self.max_retries} retries: {last_error}")

    async def get(self, path: str, params: Any = None) -> dict:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> dict:
        return await self._request("POST", path, json=json)
