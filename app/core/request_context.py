import contextvars

# Carries the current request's ID across the HTTP request and, when a task
# is enqueued, across into the TaskIQ worker process that handles it.
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
