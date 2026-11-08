"""Bounded HTTPS transport with public-only DNS resolution at connection time."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

import aiohttp


MAX_BODY = 2 * 1024 * 1024


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


def validate_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    """Validate syntax before any I/O. DNS validation happens in PublicResolver."""
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) <= 32 for c in url) or '\\' in url:
        raise ValueError('Ungültige HTTPS-Adresse')
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        raise ValueError('Ungültige HTTPS-Adresse') from None
    if (parsed.scheme != 'https' or not host or parsed.username is not None
            or parsed.password is not None or parsed.fragment or port not in (None, 443)
            or host.endswith('.') or '%' in host):
        raise ValueError('Nur öffentliche HTTPS-Adressen ohne Zugangsdaten sind erlaubt')
    if allowed_hosts is not None and host not in allowed_hosts:
        raise ValueError('Host ist für diese Quelle nicht freigegeben')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if '.' not in host or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan')):
            raise ValueError('Lokale Netzwerkziele sind gesperrt') from None
    else:
        if not _public(host):
            raise ValueError('Private Netzwerkziele sind gesperrt')
    return host


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate actual connector answers, pinning the connection to those addresses.

    A preflight DNS lookup followed by a second connector lookup would be racy.
    This resolver is the connector's resolver; no unchecked second lookup occurs.
    """
    def __init__(self):
        self.delegate = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        answers = await self.delegate.resolve(host, port, family)
        if not answers or any(not _public(answer['host']) for answer in answers):
            raise ValueError('DNS-Ziel ist nicht öffentlich; Anfrage gesperrt')
        return answers

    async def close(self):
        await self.delegate.close()


class SafeHttpClient:
    def __init__(self, min_interval: float = 3.2):
        self.min_interval = min_interval
        self._session = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._next: dict[str, float] = {}

    def _get_session(self):
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=8, ttl_dns_cache=60)
            self._session = aiohttp.ClientSession(
                connector=connector, timeout=aiohttp.ClientTimeout(total=15, connect=10),
                trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                headers={'User-Agent': 'DealChecker/0.1 (local personal price monitor)', 'Accept': 'application/json'},
            )
        return self._session

    async def _request(self, method, url, *, payload=None, allowed_hosts=None):
        host = validate_url(url, allowed_hosts)
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next.get(host, 0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next[host] = time.monotonic() + self.min_interval
            try:
                async with self._get_session().request(
                    method, url, json=payload, allow_redirects=False,
                ) as response:
                    if response.status == 429:
                        try:
                            retry_after = min(86400, max(60, float(response.headers.get('Retry-After', '60'))))
                        except ValueError:
                            retry_after = 60
                        self._next[host] = time.monotonic() + retry_after
                    if not 200 <= response.status < 300:
                        raise ValueError(f'Quelle meldet HTTP {response.status}; keine Weiterleitung verfolgt')
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > MAX_BODY:
                            raise ValueError('Antwort überschreitet das Limit von 2 MiB')
                    if method == 'POST':
                        return None
                    try:
                        result = json.loads(data)
                    except (ValueError, UnicodeError):
                        raise ValueError('Quelle liefert kein gültiges JSON') from None
                    if not isinstance(result, (dict, list)):
                        raise ValueError('Quelle liefert kein JSON-Objekt oder Array')
                    return result
            except (aiohttp.ClientError, TimeoutError, OSError):
                # aiohttp exceptions may contain webhook URLs/tokens or response content.
                raise ValueError('Netzwerkfehler oder Zeitüberschreitung bei der Quelle') from None

    async def get_json(self, url: str, *, allowed_hosts: set[str] | None = None):
        return await self._request('GET', url, allowed_hosts=allowed_hosts)

    async def post_json(self, url: str, payload: dict, *, allowed_hosts: set[str] | None = None):
        await self._request('POST', url, payload=payload, allowed_hosts=allowed_hosts)

    async def close(self):
        if self._session is not None:
            await self._session.close()

