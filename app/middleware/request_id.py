import uuid
from app.core.request_context import request_id_ctx

REQUEST_ID_HEADER = b"x-request-id"


class RequestIDMiddleware:
    """
    Plain ASGI middleware (not starlette.middleware.base.BaseHTTPMiddleware).

    BaseHTTPMiddleware's dispatch() returns - and resets the request_id
    contextvar in its `finally` - as soon as call_next() produces the response
    object, which happens before that response is actually sent over the
    socket. uvicorn's own access logger fires while the response is being
    sent, so it was seeing the already-reset "-" value instead of the real ID.
    Wrapping `send` directly keeps the contextvar bound for the whole
    request/response lifecycle, including that final send.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Reuse a client-supplied ID (e.g. OpenAI SDK's extra_headers={"x-request-id": ...})
        # so clients can correlate their own retries; otherwise generate one.
        headers = dict(scope.get("headers") or [])
        client_request_id = headers.get(REQUEST_ID_HEADER)
        request_id = client_request_id.decode() if client_request_id else f"req_{uuid.uuid4().hex}"

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response_headers = message.setdefault("headers", [])
                response_headers.append((REQUEST_ID_HEADER, request_id.encode()))
            await send(message)

        token = request_id_ctx.set(request_id)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_ctx.reset(token)
