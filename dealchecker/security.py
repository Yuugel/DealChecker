"""Loopback web boundary: Host/Origin protection, small JSON bodies and CSP."""
import re

from starlette.responses import JSONResponse


class LocalOnlyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        host = headers.get(b'host', b'').decode('latin1').lower()
        origin = headers.get(b'origin')
        same_origin = f"{scope.get('scheme', 'http')}://{host}".encode()
        if (not re.fullmatch(r'(localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?', host)
                or (origin is not None and origin != same_origin)
                or headers.get(b'sec-fetch-site') == b'cross-site'):
            return await JSONResponse({'detail':'Nur lokale Anfragen derselben Herkunft erlaubt'}, 403)(scope, receive, send)
        body = bytearray()
        if scope['method'] in {'POST','PUT','PATCH','DELETE'}:
            if scope['method'] != 'DELETE' and headers.get(b'content-type', b'').split(b';')[0].strip() != b'application/json':
                return await JSONResponse({'detail':'Content-Type application/json erforderlich'}, 415)(scope, receive, send)
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 65536:
                    return await JSONResponse({'detail':'Anfrage größer als 64 KiB'}, 413)(scope, receive, send)
                if not message.get('more_body'):
                    break
            consumed = False
            async def buffered_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {'type':'http.request', 'body':bytes(body), 'more_body':False}
                return await receive()
        else:
            buffered_receive = receive

        async def secure_send(message):
            if message['type'] == 'http.response.start':
                extra = [
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                    (b'x-content-type-options', b'nosniff'), (b'referrer-policy', b'no-referrer'),
                    (b'x-frame-options', b'DENY'), (b'cache-control', b'no-store'),
                ]
                message = {**message, 'headers':list(message.get('headers', []))+extra}
            await send(message)
        await self.app(scope, buffered_receive, secure_send)
