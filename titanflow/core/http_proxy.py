"""HTTP proxy for module outbound requests (Core side)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from titanflow.core.http import request_with_retry
from titanflow.core.config import HttpProxySettings

logger = logging.getLogger("titanflow.http_proxy")


def _domain_match(domain: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.startswith("*."):
            if domain.endswith(pattern[1:]) or domain == pattern[2:]:
                return True
        elif domain == pattern:
            return True
    return False


class HttpProxy:
    def __init__(self, settings: HttpProxySettings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=settings.timeout_seconds)

    async def request(self, url: str, method: str = "GET", headers=None, body: str | None = None) -> dict:
        headers = headers or {}
        response = await request_with_retry(
            self._client,
            method,
            url,
            headers=headers,
            content=body,
            attempts=3,
        )
        raw = response.content
        truncated = False
        if self.settings.max_body_bytes and len(raw) > self.settings.max_body_bytes:
            raw = raw[: self.settings.max_body_bytes]
            truncated = True
        encoding = response.encoding or "utf-8"
        text = raw.decode(encoding, errors="replace")
        return {
            "status": response.status_code,
            "headers": dict(response.headers),
            "body": text,
            "truncated": truncated,
        }

    @staticmethod
    def validate_domain(url: str, allowed_domains: list[str]) -> bool:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        return _domain_match(host, allowed_domains)

    async def close(self) -> None:
        await self._client.aclose()
