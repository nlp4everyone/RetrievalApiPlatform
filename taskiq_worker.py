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
# MinIO object storage service
from app.db.minio import MinioFileStore
# Qdrant vector database service
from app.db.qdrant import AsyncQdrantVectorStore
# Data schemas and types
from app.schemas.file.types import FileFormat, UploadingStatus
from langchain_core.documents import Document
# Type hints
from typing import Optional
# Text parsing services
from app.services.parsers import ParserFactory
# Text chunking services
from app.services.chunking.chonkie_chunker import (ChonkieChunkingService,
                                                   ChonkieChunkingConfig)
# Application configuration
from app.core.config import *
# Logging system
from loggers import SystemLogger
# Utility functions
from app.utils.io import async_temp_file
# Standard library components
from pathlib import Path
from datetime import datetime
import inspect

# Initialize Redis broker and result backend for task queue
result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
broker = RedisStreamBroker(url=REDIS_URL).with_result_backend(result_backend)

# Global services (will be initialized lazily)
postgres_service: Optional[any] = None
minio_service: Optional[any] = None
qdrant_service: Optional[any] = None
embedding_model: Optional[any] = None
_services_initialized: bool = False

async def initialize_services():
    """
    Initialize all core services once when first task is executed.

    This function implements lazy initialization pattern to ensure services
    are created only once, optimizing resource usage in the worker process.
    Initializes PostgreSQL, MinIO, Qdrant, and embedding model services.
    """
    global postgres_service, minio_service, qdrant_service, embedding_model, _services_initialized
    
    if _services_initialized:
        return
    
    # Postgres Service
    postgres_service = init_postgres()
    await postgres_service._create_pool()
    
    # Minio service
    minio_service = init_minio()
    # Qdrant service
    qdrant_service = init_qdrant()
    
    # Embedding model
    embedding_model = await init_embed_model(serving_service_name = EMBEDDING_SERVICE_NAME,
                                             port = DOCKER_EMBEDDING_PORT)
    
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

@broker.task
async def process_vector_store_files(vectorstore_id: str,
                                     api_key :str,
                                     file_ids: list[str],
                                     chunking_strategy: str,
                                     chunk_size: Optional[int] = None,
                                     chunk_overlap: Optional[int] = None):
    """
    Background task to process and store files in a vector store.

    Args:
        vectorstore_id (str): Unique identifier for the vector store
        api_key (str): API key for authentication and authorization
        file_ids (list[str]): List of file IDs to process
        chunking_strategy (str): Strategy for text chunking ("auto" or "static")
        chunk_size (Optional[int]): Size of text chunks for static strategy
        chunk_overlap (Optional[int]): Overlap between chunks for static strategy

    Note:
        Currently supports single file processing. Multiple file support
        is planned for future implementation.
    """
    try:
        # Step 1: Verify file existence in PostgreSQL database
        existing_files = await PostgresFileStore.check_existing_files(pool = postgres_service.pool,
                                                                      file_ids = file_ids)

        # Step 2: Prepare metadata and usage statistics
        if len(existing_files) == 0:
            # No files found - initialize empty values
            existing_file_ids = []
            usage_bytes = 0
        else:
            # Files found - collect metadata and calculate usage
            existing_file_ids = existing_files
            usage_bytes = await PostgresFileStore.get_total_bytes(pool = postgres_service.pool,
                                                                  file_ids = file_ids)

            # Step 3: Retrieve file metadata for processing
            files_metadata = await PostgresFileStore.get_metadata_for_files(pool = postgres_service.pool,
                                                                            file_ids = existing_file_ids)

            # Step 4: Process file content (currently supports single file)
            # TODO: Implement multiple file processing in future version
            if len(files_metadata) == 1:
                # Extract file extension to determine appropriate parser
                file_ext = Path(files_metadata[0].get("filename")).suffix
                # Get parser factory instance for this file type
                current_file_format, parser = ParserFactory.get(file_type = file_ext)
                # Validate parser availability
                if current_file_format is None or parser is None:
                    # Update vector store status to FAILED
                    await PostgresVectorStore.update(pool = postgres_service.pool,
                                                     vector_store_id = vectorstore_id,
                                                     api_key = api_key,
                                                     status = UploadingStatus.FAILED,
                                                     usage_bytes = usage_bytes,
                                                     last_active_at = datetime.utcnow())
                    # Raise exception for unsupported format
                    raise ValueError(f"Unsupported file format: {file_ext}")

                # Step 5: Download file from MinIO storage
                file_bytes = await MinioFileStore.download_file(minio_client = minio_service.client,
                                                                bucket_name = files_metadata[0].get("minio_bucket"),
                                                                file_path = files_metadata[0].get("minio_path"))
                
                # Step 6: Parse file content based on format
                try:
                    if current_file_format == FileFormat.TEXT:
                        # Direct text parsing for plain text files
                        file_content = await parser.parse(file_input = file_bytes)
                        SystemLogger.info(f"[WORKER] Successfully parsed text file: {files_metadata[0].get('filename')}")
                    elif current_file_format == FileFormat.PDF:
                        # PDF parsing requires temporary file handling
                        async with async_temp_file(file_bytes, suffix=".pdf") as path:
                            file_content = await parser.parse(file_input = path)
                        SystemLogger.info(f"[WORKER] Successfully parsed PDF file: {files_metadata[0].get('filename')}")
                except Exception as parse_error:
                    SystemLogger.error(f"[WORKER] Failed to parse file {files_metadata[0].get('filename')}: {str(parse_error)}")
                    # Update vector store status to FAILED due to parsing error
                    await PostgresVectorStore.update(pool = postgres_service.pool,
                                                     vector_store_id = vectorstore_id,
                                                     api_key = api_key,
                                                     status = UploadingStatus.FAILED,
                                                     usage_bytes = usage_bytes,
                                                     last_active_at = datetime.utcnow())
                    # Re-raise the exception to propagate error
                    raise
            # Placeholder for multiple file processing (TODO: Implement in future)
            elif len(files_metadata) > 1:
                file_content = ""  # Multiple files not yet supported
            else:
                file_content = ""  # No files to process

            # Step 7: Text chunking and vectorization
            # Supports both automatic and static chunking strategies
            if chunking_strategy in ["auto","static"]:
                try:
                    # Initialize chunking service with specified configuration
                    chunking_service = ChonkieChunkingService(config = ChonkieChunkingConfig(chunk_size = chunk_size))
                    # Split text into manageable chunks for embedding
                    chunked_texts = chunking_service.split_text(text = file_content)
                    # Generate vector embeddings for each text chunk
                    embeddings = await embedding_model.aembed_documents(chunked_texts)

                    # Step 8: Store documents in Qdrant vector database
                    # Initialize vector store with collection name matching vectorstore_id
                    qdrant_vector_store = AsyncQdrantVectorStore(collection_name = vectorstore_id,
                                                                 client = qdrant_service.client)

                    # Convert text chunks to LangChain Document objects with metadata
                    documents = [Document(page_content = text,
                                          metadata = {"source": file_ids[0]}) for text in chunked_texts]
                    # Insert documents and their embeddings into Qdrant collection
                    await qdrant_vector_store.insert_documents(documents = documents,
                                                               embeddings = embeddings)
                    SystemLogger.info(f"[WORKER] Successfully inserted {len(documents)} documents to Qdrant collection: {vectorstore_id}")
                except Exception as chunking_error:
                    SystemLogger.error(f"[WORKER] Failed during chunking or document insertion: {str(chunking_error)}")
                    # Update vector store status to FAILED due to chunking/vectorization error
                    await PostgresVectorStore.update(pool = postgres_service.pool,
                                                     vector_store_id = vectorstore_id,
                                                     api_key = api_key,
                                                     status = UploadingStatus.FAILED,
                                                     usage_bytes = usage_bytes,
                                                     last_active_at = datetime.utcnow())
                    # Re-raise the exception to propagate error
                    raise

        # Step 9: Update vector store status to COMPLETED on successful processing
        await PostgresVectorStore.update(pool = postgres_service.pool,
                                         vector_store_id = vectorstore_id,
                                         api_key = api_key,
                                         status = UploadingStatus.COMPLETED,
                                         usage_bytes = usage_bytes,
                                         last_active_at = datetime.utcnow())
    except Exception as e:
        # Log any unexpected errors during processing
        SystemLogger.error(f"[WORKER] Error in process_vector_store_files: {str(e)}")
        raise  # Re-raise to allow TaskIQ to handle the failure