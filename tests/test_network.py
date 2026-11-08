"""Synthetic network fixtures: no merchant traffic in the test suite."""
import asyncio
import json
import socket

import pytest

from dealchecker.network import SafeHttpClient, PublicResolver, validate_url


@pytest.mark.parametrize('url', [
    'http://example.com/a', 'https://127.0.0.1/', 'https://[::1]/',
    'https://10.0.0.1/', 'https://169.254.169.254/latest',
    'https://[::ffff:127.0.0.1]/', 'https://user:secret@example.com/',
    'https://example.com:8443/', 'https://localhost/', 'file:///c:/secret',
    'https://example.com/#secret', 'https://example.com\\@localhost/',
])
def test_rejects_unsafe_urls_before_connection(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_host_allowlist_is_exact_not_suffix():
    assert validate_url('https://itunes.apple.com/search?term=a', {'itunes.apple.com'}) == 'itunes.apple.com'
    with pytest.raises(ValueError):
        validate_url('https://itunes.apple.com.evil.example/', {'itunes.apple.com'})


async def test_resolver_rejects_mixed_public_private_dns(monkeypatch):
    resolver = PublicResolver()
    async def resolve(*args, **kwargs):
        return [{'host': ip, 'hostname': 'example.com', 'port': 443, 'family': socket.AF_INET, 'proto': 0, 'flags': 0}
                for ip in ['93.184.215.14', '127.0.0.1']]
    monkeypatch.setattr(resolver.delegate, 'resolve', resolve)
    with pytest.raises(ValueError):
        await resolver.resolve('example.com', 443)
    await resolver.close()


class Content:
    def __init__(self, body): self.body = body
    async def iter_chunked(self, size):
        for i in range(0, len(self.body), size): yield self.body[i:i+size]


class Response:
    def __init__(self, status=200, body=b'{"real_parser":true}', headers=None):
        self.status, self.content, self.headers = status, Content(body), headers or {}
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


class Session:
    closed = False
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)
    async def close(self): self.closed = True


async def test_redirects_errors_oversized_and_invalid_json_are_rejected():
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(302, headers={'Location':'https://127.0.0.1'}),
                               Response(500), Response(body=b'x' * (2*1024*1024+1)),
                               Response(body=b'not JSON')])
    for _ in range(4):
        with pytest.raises(ValueError): await client.get_json('https://example.com/feed')
    assert len(client._session.calls) == 4  # a redirect was never followed
    assert all(not x[2]['allow_redirects'] for x in client._session.calls)
    await client.close()


async def test_json_transport_and_per_host_rate_limiting():
    client = SafeHttpClient(min_interval=.04)
    client._session = Session([Response(), Response()])
    start = asyncio.get_running_loop().time()
    results = await asyncio.gather(*(client.get_json('https://example.com/feed') for _ in range(2)))
    assert results == [{'real_parser': True}] * 2
    assert asyncio.get_running_loop().time() - start >= .035
    await client.close()


async def test_post_204_does_not_require_json_and_errors_do_not_leak_url():
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(204, body=b''), Response(401)])
    await client.post_json('https://discord.com/api/webhooks/123/SECRET', {'content':'test'})
    with pytest.raises(ValueError) as exc:
        await client.post_json('https://discord.com/api/webhooks/123/SECRET', {})
    assert 'SECRET' not in str(exc.value)
    await client.close()


async def test_post_json_response_parses_bounded_json_without_changing_notification_post():
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(body=b'{"currency":"EUR"}'), Response(204, body=b''),
                               Response(body=b'invalid')])
    result = await client.post_json_response('https://modula24.com/api/checkout/shipping',
                                             {'country': 'DE'}, allowed_hosts={'modula24.com'})
    assert result == {'currency': 'EUR'}
    await client.post_json('https://discord.com/api/webhooks/123/SECRET', {'content': 'test'})
    with pytest.raises(ValueError, match='JSON'):
        await client.post_json_response('https://modula24.com/api/checkout/shipping',
                                        {}, allowed_hosts={'modula24.com'})
    assert all(not call[2]['allow_redirects'] for call in client._session.calls)
    await client.close()


async def test_larger_body_limit_requires_explicit_bounded_opt_in():
    body = json.dumps({"padding": "x" * (2 * 1024 * 1024)}).encode()
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(body=body), Response(body=body)])
    with pytest.raises(ValueError, match="Limit"):
        await client.get_json('https://modula24.com/feeds/products.de.json', allowed_hosts={'modula24.com'})
    result = await client.get_json('https://modula24.com/feeds/products.de.json',
                                   allowed_hosts={'modula24.com'}, max_body=4 * 1024 * 1024)
    assert len(result['padding']) == 2 * 1024 * 1024
    with pytest.raises(ValueError, match="Limit"):
        await client.get_json('https://modula24.com/feeds/products.de.json', max_body=9 * 1024 * 1024)
    assert len(client._session.calls) == 2
    await client.close()
