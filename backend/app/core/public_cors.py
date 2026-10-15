"""CORS for the unauthenticated public API (`/api/v1/public/*`): lead forms, consent wording, published websites.

A business's own website lives on its own domain, which the platform cannot list ahead of time, yet its visitors' browsers must be able to send an enquiry. These endpoints are
public by design: they take no cookies and no credentials, so answering any origin exposes nothing a direct request would not. Every other path keeps the strict origin list.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

PUBLIC_PREFIX = "/api/v1/public/"
_ALLOW_HEADERS = b"content-type, x-request-id"


class PublicCORSMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self._known = {o.rstrip("/") for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(PUBLIC_PREFIX):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        origin = headers.get(b"origin", b"").decode().rstrip("/")
        if not origin or origin in self._known:
            return await self.app(scope, receive, send)  # not cross-origin, or an origin the normal CORS layer already handles
        if scope["method"] == "OPTIONS" and b"access-control-request-method" in headers:
            await send({"type": "http.response.start", "status": 204, "headers": [
                (b"access-control-allow-origin", b"*"), (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
                (b"access-control-allow-headers", _ALLOW_HEADERS), (b"access-control-max-age", b"600"), (b"vary", b"Origin"),
            ]})
            return await send({"type": "http.response.body", "body": b""})

        async def send_with_cors(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"access-control-allow-origin", b"*"), (b"vary", b"Origin")]
            await send(message)

        await self.app(scope, receive, send_with_cors)
