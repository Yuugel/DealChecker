"""Synthetic network fixtures: no merchant traffic in the test suite."""
import asyncio
import json
import socket
from types import SimpleNamespace

import pytest

from dealchecker.network import SafeHttpClient, PublicResolver, validate_url, PageRedirect


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


async def test_early_timer_wakeup_cannot_shorten_host_deadline(monkeypatch):
    """Synthetic coarse timer: first wake happens before the requested deadline."""
    clock = [100.0]
    sleeps = []

    async def early_sleep(delay):
        sleeps.append(delay)
        clock[0] += 1 if len(sleeps) == 1 else delay

    monkeypatch.setattr('dealchecker.network.time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr('dealchecker.network.asyncio', SimpleNamespace(Lock=asyncio.Lock, sleep=early_sleep))
    client = SafeHttpClient(min_interval=10)
    client._session = Session([Response(), Response()])
    await client.get_json('https://example.com/feed')
    await client.get_json('https://example.com/feed')
    assert clock[0] >= 110, 'second request must wait until the original host deadline'
    assert sleeps == [10, 9]
    assert len(client._session.calls) == 2
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
    assert all('max_field_size' not in call[2] for call in client._session.calls)
    await client.close()


async def test_text_transport_is_bounded_and_does_not_follow_redirects():
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(body='<html>Größe</html>'.encode()),
                               Response(body=b'x' * 65), Response(302),
                               Response(body=b'\xff')])
    assert await client.get_text('https://example.com/product', allowed_hosts={'example.com'}) == '<html>Größe</html>'
    for limit in [64, 1024, 1024]:
        with pytest.raises(ValueError):
            await client.get_text('https://example.com/product', allowed_hosts={'example.com'}, max_body=limit)
    assert all(not call[2]['allow_redirects'] for call in client._session.calls)
    assert all(call[2]['max_field_size'] == 16384 for call in client._session.calls)
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


async def test_api_key_header_is_bounded_pinned_and_never_forwarded():
    client = SafeHttpClient(min_interval=0)
    client._session = Session([Response(), Response(302)])
    key = 'SYNTHETIC_NOT_A_REAL_REEF_KEY'
    await client.post_json_response('https://api.reefapi.com/amazon/v1/search', {},
                                    allowed_hosts={'api.reefapi.com'}, headers={'x-api-key': key})
    assert client._session.calls[0][2]['headers'] == {'x-api-key': key}
    for headers in [{'Host': key}, {'x-api-key': key + '\r\nInjected: yes'}, {'x-api-key': [key]}]:
        with pytest.raises(ValueError) as exc:
            await client.post_json_response('https://api.reefapi.com/amazon/v1/search', {},
                                           allowed_hosts={'api.reefapi.com'}, headers=headers)
        assert key not in str(exc.value)
    with pytest.raises(ValueError):
        await client.post_json_response('https://api.reefapi.com/amazon/v1/search', {}, headers={'x-api-key': key})
    with pytest.raises(ValueError, match='302'):
        await client.post_json_response('https://api.reefapi.com/amazon/v1/search', {},
                                       allowed_hosts={'api.reefapi.com'}, headers={'x-api-key': key})
    assert len(client._session.calls) == 2
    assert all(not call[2]['allow_redirects'] for call in client._session.calls)
    await client.close()


async def test_page_redirect_notice_is_opt_in_same_host_and_never_followed():
    client = SafeHttpClient(min_interval=0)
    targets = ['/de/list/washer', 'https://evil.example/list', 'https://127.0.0.1/list',
               'http://www.mediamarkt.de/list', '//www.mediamarkt.de.evil.example/list']
    client._session = Session([Response(302, headers={'Location': target}) for target in targets])
    with pytest.raises(PageRedirect) as exc:
        await client.get_text('https://www.mediamarkt.de/de/search.html', capture_redirect=True)
    assert exc.value.target == 'https://www.mediamarkt.de/de/list/washer'
    for _ in targets[1:]:
        with pytest.raises(ValueError) as exc:
            await client.get_text('https://www.mediamarkt.de/de/search.html', capture_redirect=True)
        assert not isinstance(exc.value, PageRedirect)
    assert len(client._session.calls) == len(targets)
    await client.close()
