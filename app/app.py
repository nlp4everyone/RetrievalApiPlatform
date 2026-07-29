from fastapi import FastAPI
# Define startup
from .startup import (init_embed_model,
                      init_postgres,
                      init_vector_store,
                      init_minio,
                      init_io_executor,
                      wait_for_postgres)
# Router
from .api.router import (file_router,
                        vector_store_router)
# Exception
from .exceptions import AppBaseException
from .exceptions.handlers import common_exception_handler
# Middleware
from .api.middleware import RequestIDMiddleware
# Config
from .core.config.storage import API_VERSION
from .core.config import settings
# Tracing
from .core.tracing import init_tracing
# Components
import time, logging, re
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
# Initialize FastAPI application with OpenAPI tags
app = FastAPI(openapi_tags = tags_metadata)

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

# Application Startup Event
# Initializes all required services and dependencies
@app.on_event("startup")
async def startup_event() -> None:
    SystemLogger.info("[APP] Starting application warm up...")
    
    # Validate configuration at startup
    try:
        SystemLogger.info("[APP] Validating configuration...")
        # Settings validation happens automatically on import
        # This ensures all required environment variables are present
        SystemLogger.success("[APP] ✅ Configuration validated")
    except Exception as e:
        SystemLogger.error(f"[APP] ❌ Configuration validation failed: {e}")
        raise
    
    # Start
    start = time.perf_counter()

    # Init tracing (Langfuse via OpenTelemetry OTLP)
    init_tracing()
    SystemLogger.info("[APP] ✅ Tracing ready")

    # Init embed model
    await init_embed_model()
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