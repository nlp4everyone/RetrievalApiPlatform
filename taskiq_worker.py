from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker
# Define startup
from app.startup import init_postgres, init_minio, init_embed_model, init_qdrant
# Postgres Service
from app.db.postgres import PostgresVectorStore, PostgresFileStore
# Minio service
from app.db.minio import MinioFileStore
from app.db.qdrant import AsyncQdrantVectorStore
# Schema
from app.schemas.file.types import FileFormat, UploadingStatus
# Typing
from typing import Literal, Optional
# Text Parser
from app.services.parsers import ParserFactory
from app.services.chunking.chonkie_chunker import (ChonkieChunkingService,
                                                   ChonkieChunkingConfig)
# Utils
from app.utils.io import async_temp_file
# Other components
from pathlib import Path
from langchain_core.documents import Document
from datetime import datetime
# Config
from app.core.config.service_params import *
# Logger
from loggers import SystemLogger

REDIS_URL = "redis://redis:6379"

result_backend = RedisAsyncResultBackend(redis_url=REDIS_URL)
broker = RedisStreamBroker(url=REDIS_URL).with_result_backend(result_backend)
# Postgres Service
postgres_service = init_postgres()
minio_service = init_minio()
qdrant_service = init_qdrant()

@broker.task
async def process_vector_store_files(vectorstore_id: str,
                                     api_key :str,
                                     file_ids: list[str],
                                     chunking_strategy: Literal["auto","static"],
                                     chunk_size: Optional[int] = None,
                                     chunk_overlap: Optional[int] = None):
    """
    Background task to process vector store files.
    Validates file IDs and updates vector store status accordingly.
    """
    # Wait for postgres
    await postgres_service._create_pool()
    embedding_model = await init_embed_model(serving_service_name = EMBEDDING_SERVING_SERVICE_NAME,
                                             port = 30001)
    try:
        # Check file existance
        existing_files = await PostgresFileStore.check_existing_files(pool = postgres_service.pool,
                                                                      file_ids = file_ids)

        # Define value for update
        if len(existing_files) == 0:
            # In case no files exited
            existing_file_ids = []
            usage_bytes = 0
        else:
            existing_file_ids = existing_files
            usage_bytes = await PostgresFileStore._get_total_bytes_for_file_ids(pool = postgres_service.pool,
                                                                                file_ids = file_ids)

            # Get metadata
            files_metadata = await PostgresFileStore._get_metadata_for_files(pool = postgres_service.pool,
                                                                             file_ids = existing_file_ids)

            # Get file content
            # Chunk only 1 file
            if len(files_metadata) == 1:
                # Get file extension
                file_ext = Path(files_metadata[0].get("filename")).suffix
                # Get parser
                current_file_format, parser = ParserFactory.get(file_type = file_ext)
                # Check status
                if current_file_format is None or parser is None:
                    # Update failed status to vector store
                    await PostgresVectorStore.update(pool = postgres_service.pool,
                                                     vector_store_id = vectorstore_id,
                                                     api_key = api_key,
                                                     status = UploadingStatus.FAILED,
                                                     usage_bytes = usage_bytes,
                                                     last_active_at = datetime.utcnow())
                    # Raise exception
                    raise ValueError(f"Unsupported file format: {file_ext}")

                # Get as bytes
                file_bytes = await MinioFileStore._load_file(minio_client = minio_service.client,
                                                             bucket_name = files_metadata[0].get("minio_bucket"),
                                                             file_path = files_metadata[0].get("minio_path"))
                if current_file_format == FileFormat.TEXT:
                    # Get file content
                    file_content = await parser.parse(file_input = file_bytes)
                elif current_file_format == FileFormat.PDF:
                    # Get file content
                    async with async_temp_file(file_bytes, suffix=".pdf") as path:
                        # Get file content
                        file_content = await parser.parse(file_input = path)
            # Chunk multiple files ( Do later)
            elif len(files_metadata) > 1:
                file_content = ""
            else:
                file_content = ""

            # Chunking with base splitter
            if chunking_strategy in ["auto","static"]:
                # Define service
                chunking_service = ChonkieChunkingService(config = ChonkieChunkingConfig(chunk_size = chunk_size))
                # Split
                chunked_texts = chunking_service.split_text(text = file_content)

                # Get embedding
                embeddings = await embedding_model.aembed_documents(chunked_texts)

                # Attach to vector store
                qdrant_vector_store = AsyncQdrantVectorStore(collection_name = vectorstore_id,
                                                             client = qdrant_service.client)

                # Convert to Document
                documents = [Document(page_content = text,
                                      metadata = {"source": file_ids[0]}) for text in chunked_texts]
                # Insert to Qdrant
                await qdrant_vector_store.insert_documents(documents = documents,
                                                           embeddings = embeddings)

            # Get Qdrant Vector store

            # Update vector store
            await PostgresVectorStore.update(pool = postgres_service.pool,
                                             vector_store_id = vectorstore_id,
                                             api_key = api_key,
                                             status = UploadingStatus.COMPLETED,
                                             usage_bytes = usage_bytes,
                                             last_active_at = datetime.utcnow())
    except Exception as e:
        SystemLogger.info(e)