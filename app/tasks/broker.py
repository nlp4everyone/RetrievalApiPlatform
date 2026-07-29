"""RedisStreamBroker for background ingestion work.

This module only owns the broker's lifecycle (connect, bootstrap services on
worker startup, close them on shutdown). The task itself lives in
app.tasks.ingestion_task, which is registered against this broker on import -
keeping the two apart is what lets this module be the deploy entrypoint
(``taskiq worker app.tasks.broker:broker``) without pulling in the ingestion
pipeline just to start the worker process.
"""
from taskiq import TaskiqEvents, TaskiqState
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.core.config import REDIS_URL
from app.core.tracing import init_tracing
from app.db.vector_store import VectorStoreFactory
from app.startup import (get_postgres_client,
                         init_cpu_executor,
                         init_download_semaphore,
                         init_embed_model,
                         init_io_executor,
                         init_minio,
                         init_parsing_service,
                         init_postgres,
                         init_vector_store)
from loggers import SystemLogger

result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
broker = RedisStreamBroker(url=REDIS_URL).with_result_backend(result_backend)

_initialized = False


async def _initialize_services() -> None:
    """Bootstrap every service this worker process needs, once.

    Mirrors app.app's startup_event so both processes end up with the same
    live services, reached afterwards through the same app.startup getters
    the HTTP-side services already use - no worker-local globals.
    """
    global _initialized
    if _initialized:
        return

    # Tracing (Langfuse via OpenTelemetry OTLP)
    init_tracing()

    # Postgres
    postgres_client = init_postgres()
    await postgres_client._create_pool()

    # Minio
    init_minio()
    # Thread pools: I/O (MinIO, chunking-adjacent network calls) kept apart
    # from CPU (chunking), plus the download concurrency cap - this worker
    # process runs the whole ingestion pipeline, so it needs all three
    init_io_executor()
    init_cpu_executor()
    init_download_semaphore()
    # Vector store (registers itself with VectorStoreFactory)
    await init_vector_store()
    # Embedding model
    await init_embed_model()
    # Parsing providers
    init_parsing_service()

    _initialized = True
    SystemLogger.info("[WORKER] TaskIQ is ready")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def worker_startup(state: TaskiqState) -> None:
    await _initialize_services()


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def worker_shutdown(state: TaskiqState) -> None:
    if _initialized:
        await get_postgres_client().close()

    # Close every registered vector store client
    await VectorStoreFactory.close_all()