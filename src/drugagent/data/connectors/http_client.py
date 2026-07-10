from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx


class JsonHttpClient:
    def __init__(self, timeout_s: float = 20.0, retries: int = 2, backoff_base_s: float = 0.5):
        self.timeout_s = timeout_s
        self.retries = retries
        self.backoff_base_s = backoff_base_s

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                    response = await client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_payload,
                        headers=headers,
                    )
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.HTTPError):
                if attempt >= self.retries:
                    raise
                await asyncio.sleep((self.backoff_base_s * (2**attempt)) + random.uniform(0, 0.2))
                attempt += 1

    async def post_json(
        self,
        url: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request_with_retry(
            "POST",
            url,
            json_payload=payload,
            headers=headers,
        )
        return response.json()

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request_with_retry(
            "GET",
            url,
            params=params,
            headers=headers,
        )
        return response.json()

    async def get_text(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        response = await self._request_with_retry(
            "GET",
            url,
            params=params,
            headers=headers,
        )
        return response.text
