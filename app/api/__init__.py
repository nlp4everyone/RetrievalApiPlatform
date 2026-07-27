"""
The HTTP boundary: everything that exists only because this app is served over HTTP.

Routers declare the endpoints, and the three modules beside them are the FastAPI
plumbing those endpoints need - request-scoped validation, the auth dependency,
and the ASGI middleware. Nothing outside this package imports them.

Business logic lives one layer down in app.services; app.components and
app.pipelines never reach up here at all, which is what lets the TaskIQ worker
run without FastAPI.

    router/           /v1/files, /v1/vector_stores
    dependencies.py   upload validation used via Depends
    security.py       API key verification
    middleware.py     X-Request-Id correlation
"""
