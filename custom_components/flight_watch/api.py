"""FR24 (unofficial) and AeroDataBox HTTP calls."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

_LOGGER = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
FR24_LIST = "https://api.flightradar24.com/common/v1/flight/list.json"
FR24_LIVE = "https://data-live.flightradar24.com/clickhandler/"
ADB = "https://aerodatabox.p.rapidapi.com/flights/number/{number}/{day}"
SPACING = 2.0  # seconds between FR24 requests; FR24 rate limits by IP


class RateLimited(Exception):
    """FR24 said slow down (429/402)."""


class Api:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.paused_until = 0.0

    async def _fr24(self, url: str, params: dict) -> dict:
        if time.monotonic() < self.paused_until:
            raise RateLimited
        async with self._lock:
            wait = self._last + SPACING - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session.get(
                    url, params=params, headers={"User-Agent": UA}, timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    if r.status in (402, 429):
                        self.paused_until = time.monotonic() + 600
                        _LOGGER.warning("FR24 rate limit (HTTP %s), pausing 10 min", r.status)
                        raise RateLimited
                    r.raise_for_status()
                    return await r.json(content_type=None)
            finally:
                self._last = time.monotonic()

    async def flight_list(self, number: str) -> dict:
        return await self._fr24(FR24_LIST, {"query": number, "fetchBy": "flight", "page": 1, "limit": 100})

    async def live(self, fr24_id: str) -> dict:
        return await self._fr24(FR24_LIVE, {"version": "1.5", "flight": fr24_id})

    async def adb(self, key: str, number: str, day: str) -> list | None:
        """AeroDataBox schedule for one day; None when it has no data (HTTP 204)."""
        async with self._session.get(
            ADB.format(number=number, day=day),
            params={"withAircraftImage": "false", "withLocation": "false"},
            headers={"x-rapidapi-key": key, "x-rapidapi-host": "aerodatabox.p.rapidapi.com"},
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            if r.status == 204:
                return None
            r.raise_for_status()
            data = await r.json(content_type=None)
            return data if isinstance(data, list) else None
