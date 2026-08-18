from fastapi import FastAPI
# Define startup
from .startup import (init_embed_model,
                      init_sparse_embed_model,
                      close_embed_model,
                      close_sparse_embed_model,
                      init_postgres,
                      init_vector_store,
                      init_minio,
                      init_io_executor,
                      get_io_executor,
                      get_postgres_client,
                      wait_for_postgres)
# Vector store connections opened at startup, closed on the way out
from .db.vector_store import VectorStoreFactory
# Router
from .api.router import (file_router,
                        vector_store_router)
# Exception
from .exceptions import AppBaseException
from .exceptions.handlers import common_exception_handler
# Middleware
from .api.middleware import RequestIDMiddleware
# Config
from .core.config.api import API_VERSION
# Tracing
from .core.tracing import init_tracing
# Components
import time, logging, re
from contextlib import asynccontextmanager
from typing import AsyncIterator
# Logger
from loggers import SystemLogger

# Silence successful /health probe pings; still log them when they fail
class HealthCheckLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if len(record.args) < 5:
            return True
        _, _, path, _, status_code = record.args
        return not (path == "/health" and 200 <= status_code < 300)

logging.getLogger("uvicorn.access").addFilter(HealthCheckLogFilter())

# Silence successful (2xx) access log lines for high-frequency list/retrieve/
# modify calls - they're already logged at DEBUG/INFO by SystemLogger in the
# service layer. Failures still show up here for debugging. create/delete/
# search stay visible on the access log regardless of status.
_QUIET_ACCESS_ROUTES = (
    ("GET", re.compile(rf"^/{API_VERSION}/vector_stores(\?.*)?$")),        # list
    ("GET", re.compile(rf"^/{API_VERSION}/vector_stores/[^/]+(\?.*)?$")),  # retrieve
    ("POST", re.compile(rf"^/{API_VERSION}/vector_stores/[^/]+(\?.*)?$")), # modify
    ("GET", re.compile(rf"^/{API_VERSION}/files(\?.*)?$")),                # list
    ("GET", re.compile(rf"^/{API_VERSION}/files/[^/]+(\?.*)?$")),          # retrieve
)

class QuietAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if len(record.args) < 5:
            return True
        _, method, path, _, status_code = record.args
        if not (200 <= status_code < 300):
            return True
        return not any(method == m and pattern.match(path) for m, pattern in _QUIET_ACCESS_ROUTES)

logging.getLogger("uvicorn.access").addFilter(QuietAccessLogFilter())


# Define OpenAPI tags for API documentation
tags_metadata = [
    {
        "name": "File",
        "description": "Provides a secure way to upload, manage, and retrieve files",
    },
    {
        "name": "Vector Stores",
        "description": "Provides a secure way to create, manage, and retrieve vector store",
    },
]

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Bring every service this process needs up, then tear it back down.

    Startup and shutdown live in one function so a connection opened above the
    yield cannot be forgotten below it - the worker already closes the same two
    clients in broker.py's WORKER_SHUTDOWN handler.
    """
    SystemLogger.info("[APP] Starting application warm up...")

    # Settings are validated on import of app.core.config, so reaching this
    # point already means every required environment variable is present.

    # Start
    start = time.perf_counter()

    # Init tracing (Langfuse via OpenTelemetry OTLP)
    init_tracing()
    SystemLogger.info("[APP] ✅ Tracing ready")

    # Init embed model
    await init_embed_model()
    # Init sparse embed model (no-op unless SPARSE_EMBEDDING_ENABLED)
    await init_sparse_embed_model()
    SystemLogger.info("[APP] ✅ Serving embedding ready!")

    # Init Postgres
    postgres_client = init_postgres()
    # Create pool and wait for postgres
    await postgres_client._create_pool()
    # Wait for postgres
    await wait_for_postgres(postgres_client.pool)
    # Create table if not existed
    await postgres_client._create_table()
    SystemLogger.info("[APP] ✅ Postgres ready")
    # Init vector store (provider from VECTOR_STORE_PROVIDER)
    await init_vector_store()
    SystemLogger.info("[APP] ✅ Vector store ready")
    # Init Minio
    init_minio()
    SystemLogger.info("[APP] ✅ MinIO ready")
    # Init I/O thread pool (used by MinIO upload/delete on this process)
    init_io_executor()
    SystemLogger.info("[APP] ✅ I/O thread pool ready")
    # Logging
    SystemLogger.success(f"[APP] Service started in {round(time.perf_counter() - start,1)}s")

    yield

    # Shutdown: release what the block above acquired, in reverse order. The
    # tracer provider is not touched - its own atexit handler flushes the
    # pending spans, and doing it here would cut the shutdown logs short
    SystemLogger.info("[APP] Shutting down...")
    # Drop the I/O threads first so nothing new reaches MinIO or the clients below
    get_io_executor().shutdown(wait = False, cancel_futures = True)
    await VectorStoreFactory.close_all()
    await get_postgres_client().close()
    await close_embed_model()
    await close_sparse_embed_model()
    SystemLogger.success("[APP] Shutdown complete")


# Initialize FastAPI application with OpenAPI tags
app = FastAPI(openapi_tags = tags_metadata,
              lifespan = lifespan)

# Tag every request with a correlation ID (X-Request-Id), echoed back to the
# client and bound to all logs emitted while handling that request
app.add_middleware(RequestIDMiddleware)

# Register API Routes
# Add file router for file upload and management operations
app.include_router(file_router,
                   prefix = f"/{API_VERSION}",
                   tags = [tags_metadata[0].get("name")])
# Add vector store router for vector database operations
app.include_router(vector_store_router,
                   prefix = f"/{API_VERSION}",
                   tags = [tags_metadata[1].get("name")])

# Register global exception handler for custom exceptions
app.add_exception_handler(AppBaseException, common_exception_handler)

@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}

