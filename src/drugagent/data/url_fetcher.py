from __future__ import annotations

import html
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

import httpx


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_URL_RE = re.compile(r"https?://[^\s)\\]}>]+", flags=re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    urls = [u.strip().rstrip(".,;") for u in _URL_RE.findall(text)]
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
    )


def _host_is_blocked(host: str) -> bool:
    host_l = (host or "").strip().lower()
    if not host_l:
        return True
    if host_l in {"localhost"}:
        return True
    if host_l.endswith(".localhost"):
        return True
    if host_l in {"127.0.0.1", "::1"}:
        return True
    if _is_private_ip(host_l):
        return True
    return False


def _resolves_to_private_ip(host: str) -> bool:
    # Best-effort DNS resolution to mitigate SSRF via public hostname -> private IP.
    # Disable via env if needed.
    if os.getenv("A4T_URL_FETCH_DNS_CHECK", "1").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = str(sockaddr[0])
        if _is_private_ip(ip):
            return True
    return False


def validate_user_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only http(s) URLs are allowed.")
    if not parsed.netloc:
        raise ValueError("URL must include a hostname.")
    host = parsed.hostname or ""
    if _host_is_blocked(host) or _resolves_to_private_ip(host):
        raise ValueError("URL hostname is blocked for safety (localhost/private network).")


def _html_to_text(body: str) -> str:
    # Minimal HTML stripping; keep it dependency-free.
    body = re.sub(r"(?is)<script.*?>.*?</script>", " ", body)
    body = re.sub(r"(?is)<style.*?>.*?</style>", " ", body)
    body = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", body)
    body = re.sub(r"(?is)<title.*?>(.*?)</title>", " ", body)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


@dataclass(frozen=True)
class UrlResource:
    url: str
    content_type: str
    title: str | None
    text: str
    bytes_fetched: int


class UrlResourceFetcher:
    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self.max_urls = max(1, _int_env("A4T_FOLLOWUP_MAX_URLS", 5))
        self.max_bytes = max(50_000, _int_env("A4T_URL_FETCH_MAX_BYTES", 2_000_000))
        self.timeout_s = max(1.0, _float_env("A4T_URL_FETCH_TIMEOUT_S", 15.0))
        self.client = client

    async def fetch(self, urls: Iterable[str]) -> list[UrlResource]:
        unique: list[str] = []
        seen: set[str] = set()
        for url in urls:
            u = (url or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            unique.append(u)
        unique = unique[: self.max_urls]

        for url in unique:
            validate_user_url(url)

        if not unique:
            return []

        close_client = False
        client = self.client
        if client is None:
            close_client = True
            client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_s), follow_redirects=False, headers={"user-agent": "Drugagent/0.1"})

        out: list[UrlResource] = []
        try:
            for url in unique:
                # Manual redirect handling with re-validation.
                current = url
                for _ in range(3):
                    resp = await client.get(current)
                    if resp.status_code in {301, 302, 303, 307, 308}:
                        nxt = resp.headers.get("location")
                        if not nxt:
                            break
                        validate_user_url(nxt)
                        current = nxt
                        continue
                    break

                content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                if content_type not in {"text/html", "text/plain"}:
                    continue

                raw = resp.content[: self.max_bytes + 1]
                if len(raw) > self.max_bytes:
                    raw = raw[: self.max_bytes]

                body = raw.decode(resp.encoding or "utf-8", errors="replace")
                text = _html_to_text(body) if content_type == "text/html" else body.strip()

                title = None
                if content_type == "text/html":
                    m = re.search(r"(?is)<title.*?>(.*?)</title>", body)
                    if m:
                        title = html.unescape(re.sub(r"\s+", " ", m.group(1)).strip()) or None

                out.append(
                    UrlResource(
                        url=current,
                        content_type=content_type,
                        title=title,
                        text=text[: 20_000],
                        bytes_fetched=len(raw),
                    )
                )
        finally:
            if close_client:
                await client.aclose()
        return out
