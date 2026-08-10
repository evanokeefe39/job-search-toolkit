"""Shared LLM client with retry logic and rate limiting.

Uses the OpenAI-compatible chat completions API.
Configure via environment variables: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_RPM, LLM_CONCURRENCY


class LLMClient:
    """Async LLM client with semaphore-based concurrency control."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Async client; params override the module defaults (config chain).

        Backward-compatible: ``LLMClient()`` uses LLM_MODEL/LLM_BASE_URL/
        LLM_API_KEY from pipeline.config as before. The tailor pipeline passes
        its resolved (CLI > env > config.yaml) values explicitly so the
        fallback honors overrides.
        """
        self._model = model or LLM_MODEL
        key = api_key or LLM_API_KEY
        if not key:
            raise RuntimeError(
                "LLM_API_KEY not set. Export it or add it to a .env file."
            )
        self._client = httpx.AsyncClient(
            base_url=base_url or LLM_BASE_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0),
        )
        self._sem = asyncio.Semaphore(LLM_CONCURRENCY)
        self._min_interval = 60.0 / LLM_MAX_RPM  # seconds between requests
        self._last_request = 0.0
    async def close(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        """Send a single completion request. Retries on transient errors."""
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with self._sem:
                    # Rate limit: ensure minimum interval between requests
                    now = time.monotonic()
                    wait = self._last_request + self._min_interval - now
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_request = time.monotonic()

                    resp = await self._client.post("/chat/completions", json=body)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("retry-after", "5"))
                    await asyncio.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]

            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    last_error = e
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise
            except (httpx.RequestError, json.JSONDecodeError) as e:
                last_error = e
                await asyncio.sleep(2 ** attempt)

        raise last_error  # type: ignore[misc]

    async def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Send a completion request and parse the response as JSON."""
        text = await self.complete(
            prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return json.loads(text)

    async def batch_complete(
        self,
        prompts: list[str],
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> list[str]:
        """Process multiple prompts concurrently (bounded by LLM_CONCURRENCY)."""
        tasks = [
            self.complete(
                p,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
            for p in prompts
        ]
        return list(await asyncio.gather(*tasks))

    async def batch_complete_json(
        self,
        prompts: list[str],
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> list[dict[str, Any]]:
        """Process multiple prompts concurrently, parsing each as JSON."""
        texts = await self.batch_complete(
            prompts,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return [json.loads(t) for t in texts]
