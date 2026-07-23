# TaskIQ Components - Distributed task queue system
from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker
from taskiq import TaskiqEvents, TaskiqState
# Application startup initialization functions
from app.startup import (init_postgres,
                         init_minio,
                         init_embed_model,
                         init_qdrant)
# Database services
from app.db.postgres import PostgresVectorStore, PostgresFileStore
# Qdrant vector database service
from app.db.qdrant import AsyncQdrantVectorStore
# Data schemas and types
from app.schemas.file.types import UploadingStatus
# Type hints
from typing import Optional
# Vector store ingest pipeline (embed + upsert, file load + chunk)
from app.services.ingest.ingest_pipeline import embed_and_upload_chunks
from app.services.ingest.file_loader import load_and_chunk_file
# Application configuration
from app.core.config import *
# Tracing
from app.core.tracing import init_tracing
# Logging system
from loggers import SystemLogger
# Request correlation ID
from app.core.request_context import request_id_ctx
# Standard library components
from datetime import datetime
import inspect

# Initialize Redis broker and result backend for task queue
result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
broker = RedisStreamBroker(url=REDIS_URL).with_result_backend(result_backend)

# Global services (will be initialized lazily)
postgres_service: Optional[any] = None
minio_service: Optional[any] = None
qdrant_service: Optional[any] = None
_services_initialized: bool = False

async def initialize_services():
    """
    Initialize all core services once when first task is executed.

    This function implements lazy initialization pattern to ensure services
    are created only once, optimizing resource usage in the worker process.
    Initializes PostgreSQL, MinIO, Qdrant, and embedding model services.
    """
    global postgres_service, minio_service, qdrant_service, _services_initialized

    if _services_initialized:
        return

    # Tracing (Langfuse via OpenTelemetry OTLP)
    init_tracing()

    # Postgres Service
    postgres_service = init_postgres()
    await postgres_service._create_pool()

    # Minio service
    minio_service = init_minio()
    # Qdrant service
    qdrant_service = await init_qdrant()

    # Embedding model
    await init_embed_model()

    _services_initialized = True
    SystemLogger.info("[WORKER] TaskIQ is ready")


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def worker_startup(state: TaskiqState) -> None:
    """
    Event handler called when TaskIQ worker starts up.
    
    This function is automatically called by TaskIQ when the worker process
    begins running. It initializes all required services to ensure the worker
    is ready to process tasks.
    
    Args:
        state (TaskiqState): Current state of the TaskIQ worker
    """
    await initialize_services()


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def worker_shutdown(state: TaskiqState) -> None:
    """
    Event handler called when TaskIQ worker shuts down gracefully.
    
    This function ensures proper cleanup of resources by closing database
    connections and other service clients to prevent resource leaks.
    
    Args:
        state (TaskiqState): Current state of the TaskIQ worker
    """
    global postgres_service, qdrant_service

    # Close PostgreSQL connection pool if initialized
    if postgres_service is not None:
        await postgres_service.close()

    # Close Qdrant client connection if initialized
    # Use introspection to handle both sync and async close methods
    if qdrant_service is not None and hasattr(qdrant_service, "client"):
        close_fn = getattr(qdrant_service.client, "close", None)
        if close_fn is not None:
            if inspect.iscoroutinefunction(close_fn):
                await close_fn()  # Async close method
            else:
                close_fn()       # Sync close method


async def _mark_failed(vectorstore_id: str, api_key: str, usage_bytes: int) -> None:
    await PostgresVectorStore.update(pool = postgres_service.pool,
                                     vector_store_id = vectorstore_id,
                                     api_key = api_key,
                                     status = UploadingStatus.FAILED,
                                     usage_bytes = usage_bytes,
                                     last_active_at = datetime.utcnow())


@broker.task
async def process_vector_store_files(vectorstore_id: str,
                                     api_key :str,
                                     file_ids: list[str],
                                     chunking_strategy: str,
                                     chunk_size: Optional[int] = None,
                                     chunk_overlap: Optional[int] = None,
                                     request_id: str = "-"):
    """
    Background task to process and store files in a vector store.

    Args:
        vectorstore_id (str): Unique identifier for the vector store
        api_key (str): API key for authentication and authorization
        file_ids (list[str]): List of file IDs to process
        chunking_strategy (str): Strategy for text chunking ("auto" or "static")
        chunk_size (Optional[int]): Size of text chunks for static strategy
        chunk_overlap (Optional[int]): Overlap between chunks for static strategy
        request_id (str): ID of the HTTP request that enqueued this task, so
                          worker logs can be correlated with the originating request

    Note:
        Currently supports single file processing. Multiple file support
        is planned for future implementation.
    """
    token = request_id_ctx.set(request_id)
    SystemLogger.info(f"[WORKER] Start processing vector store {vectorstore_id} (files: {file_ids})")
    usage_bytes = 0
    try:
        # Step 1: Verify which requested files actually exist
        existing_file_ids = await PostgresFileStore.check_existing_files(pool = postgres_service.pool,
                                                                         file_ids = file_ids)

        if existing_file_ids:
            usage_bytes = await PostgresFileStore.get_total_bytes(pool = postgres_service.pool,
                                                                  file_ids = file_ids)
            files_metadata = await PostgresFileStore.get_metadata_for_files(pool = postgres_service.pool,
                                                                            file_ids = existing_file_ids)

            # Step 2: Load, parse, and chunk file content (single-file support only)
            # TODO: Implement multiple file processing in future version
            chunked_texts = []
            if len(files_metadata) == 1:
                try:
                    chunked_texts = await load_and_chunk_file(minio_service.client, files_metadata[0], chunk_size)
                except Exception as load_error:
                    SystemLogger.error(f"[WORKER] Failed to load/parse file {files_metadata[0].get('filename')}: {str(load_error)}")
                    await _mark_failed(vectorstore_id, api_key, usage_bytes)
                    raise

            # Step 3: Embed chunks and upload them to Qdrant
            if chunking_strategy in ["auto", "static"] and chunked_texts:
                try:
                    qdrant_vector_store = AsyncQdrantVectorStore(collection_name = vectorstore_id,
                                                                 client = qdrant_service.client)
                    total_inserted = await embed_and_upload_chunks(qdrant_vector_store = qdrant_vector_store,
                                                                    chunked_texts = chunked_texts,
                                                                    source_file_id = file_ids[0],
                                                                    vectorstore_id = vectorstore_id,
                                                                    api_key = api_key)
                    SystemLogger.info(f"[WORKER] Successfully inserted {total_inserted} documents to Qdrant collection: {vectorstore_id}")
                except Exception as chunking_error:
                    SystemLogger.error(f"[WORKER] Failed during chunking or document insertion: {str(chunking_error)}")
                    await _mark_failed(vectorstore_id, api_key, usage_bytes)
                    raise

        # Step 4: Mark vector store as completed
        await PostgresVectorStore.update(pool = postgres_service.pool,
                                         vector_store_id = vectorstore_id,
                                         api_key = api_key,
                                         status = UploadingStatus.COMPLETED,
                                         usage_bytes = usage_bytes,
                                         last_active_at = datetime.utcnow())
        SystemLogger.success(f"[WORKER] Vector store {vectorstore_id} processing completed")
    except Exception as e:
        # Log any unexpected errors during processing
        SystemLogger.error(f"[WORKER] Error in process_vector_store_files: {str(e)}")
        raise  # Re-raise to allow TaskIQ to handle the failure
    finally:
        request_id_ctx.reset(token)