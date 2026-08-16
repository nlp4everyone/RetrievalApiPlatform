"""Business logic behind the vector-store ingestion background task.

Kept separate from app.tasks: a plain async class with no TaskIQ import, so
it can be called - and tested - without a running broker.
app.tasks.ingestion_task is a thin adapter around
IngestionService.ingest_vector_store_files.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.tracing import TraceCarrier
from app.db.postgres import PostgresFileStore, PostgresVectorStore
from app.db.vector_store import VectorStoreFactory
from app.pipelines.ingestion import IngestionContext, build_ingestion_pipeline
from app.schemas.file.types import UploadingStatus
from app.startup import (get_dense_embedding,
                         get_minio_service,
                         get_parsing_service,
                         get_postgres_pool,
                         get_sparse_embedding,
                         is_sparse_embedding_enabled)
from loggers import SystemLogger

# The strategy names VectorStoreService.create can put on the queue. Both resolve
# to the same splitter today and differ only in sizing, but an unrecognised value
# means producer and worker have drifted apart, so the run fails instead of no-oping.
_SUPPORTED_CHUNKING_STRATEGIES = frozenset({"auto", "static"})


class IngestionService:
    """Orchestrates ingesting the file(s) behind one vector store."""

    @staticmethod
    async def ingest_vector_store_files(vectorstore_id: str,
                                        api_key: str,
                                        file_ids: list[str],
                                        chunking_strategy: str,
                                        chunk_size: Optional[int],
                                        chunk_overlap: Optional[int],
                                        vector_store_type: str,
                                        trace_context: Optional[TraceCarrier]) -> None:
        """
        Run ingestion for a vector store's file(s) and record the outcome.

        Args:
            vectorstore_id: Unique identifier for the vector store
            api_key: API key for authentication and authorization
            file_ids: List of file IDs to process
            chunking_strategy: Strategy for text chunking ("auto" or "static")
            chunk_size: Size of text chunks for static strategy
            chunk_overlap: Overlap between chunks for static strategy
            vector_store_type: Backend the vector store was created with
            trace_context: W3C trace context from the request that queued
                this run, so ingestion spans land inside its Langfuse trace

        Raises:
            Exception: Whatever step failed; the vector store is marked
                FAILED before the exception is re-raised

        Note:
            COMPLETED means the collection is searchable, so every path that
            leaves it empty - an unknown chunking strategy, files that do not
            exist, a file that yields no chunks - fails instead. The one
            legitimately empty store is the one created with no file_ids at all.

            Currently supports single file processing. VectorStoreService.create
            already rejects more than one file_id at request time; the check
            here is a second line of defense.
        """
        SystemLogger.info(f"[WORKER] Start processing vector store {vectorstore_id} (files: {file_ids})")
        postgres_pool = get_postgres_pool()
        usage_bytes = 0

        # An unknown strategy used to fall through the ingest branch untouched and
        # still report COMPLETED, so reject it here rather than skipping the work
        if chunking_strategy not in _SUPPORTED_CHUNKING_STRATEGIES:
            SystemLogger.error(f"[WORKER] Vector store {vectorstore_id} was queued with unsupported "
                               f"chunking strategy {chunking_strategy!r}")
            await IngestionService._mark_failed(vectorstore_id, api_key, usage_bytes)
            raise ValueError(f"Unsupported chunking strategy: {chunking_strategy!r}")

        # Step 1: Verify which requested files actually exist
        existing_file_ids = await PostgresFileStore.check_existing_files(pool = postgres_pool,
                                                                         file_ids = file_ids)

        # A store created without files is legitimately empty; one whose files
        # cannot be found is not, and reporting COMPLETED there hides the failure
        if file_ids and not existing_file_ids:
            SystemLogger.error(f"[WORKER] None of the files requested for vector store "
                               f"{vectorstore_id} exist: {file_ids}")
            await IngestionService._mark_failed(vectorstore_id, api_key, usage_bytes)
            raise ValueError(f"No such file(s) for ingestion: {file_ids}")

        if existing_file_ids:
            usage_bytes = await PostgresFileStore.get_total_bytes(pool = postgres_pool,
                                                                  file_ids = existing_file_ids)
            files_metadata = await PostgresFileStore.get_metadata_for_files(pool = postgres_pool,
                                                                            file_ids = existing_file_ids)

            # Step 2: Run the ingestion pipeline (single-file support only)
            # TODO: Implement multiple file processing in future version
            if len(files_metadata) != 1:
                SystemLogger.error(f"[WORKER] Vector store {vectorstore_id} requested "
                                   f"{len(files_metadata)} file(s) to ingest; only single-file "
                                   f"processing is supported")
                await IngestionService._mark_failed(vectorstore_id, api_key, usage_bytes)
                raise ValueError(f"Unsupported number of files for ingestion: {len(files_metadata)}")

            try:
                num_inserted = await IngestionService._ingest_single_file(vectorstore_id = vectorstore_id,
                                                                          api_key = api_key,
                                                                          file_id = existing_file_ids[0],
                                                                          file_metadata = files_metadata[0],
                                                                          chunking_strategy = chunking_strategy,
                                                                          chunk_size = chunk_size,
                                                                          chunk_overlap = chunk_overlap,
                                                                          vector_store_type = vector_store_type,
                                                                          trace_context = trace_context)
            except Exception as ingestion_error:
                SystemLogger.error(f"[WORKER] Ingestion failed for vector store {vectorstore_id}: {ingestion_error}")
                await IngestionService._mark_failed(vectorstore_id, api_key, usage_bytes)
                raise

            # The pipeline runs to the end on a file it cannot get any text out of,
            # which leaves the collection empty - not something to call COMPLETED
            if num_inserted == 0:
                SystemLogger.error(f"[WORKER] Ingestion produced no documents for vector store "
                                   f"{vectorstore_id}; nothing was written to the collection")
                await IngestionService._mark_failed(vectorstore_id, api_key, usage_bytes)
                raise ValueError(f"Ingestion produced no documents for vector store {vectorstore_id}")

        # Step 3: Mark vector store as completed
        await PostgresVectorStore.update(pool = postgres_pool,
                                         vector_store_id = vectorstore_id,
                                         api_key = api_key,
                                         status = UploadingStatus.COMPLETED,
                                         usage_bytes = usage_bytes,
                                         last_active_at = datetime.now(timezone.utc))
        SystemLogger.success(f"[WORKER] Vector store {vectorstore_id} processing completed")

    @staticmethod
    async def _mark_failed(vectorstore_id: str, api_key: str, usage_bytes: int) -> None:
        await PostgresVectorStore.update(pool = get_postgres_pool(),
                                         vector_store_id = vectorstore_id,
                                         api_key = api_key,
                                         status = UploadingStatus.FAILED,
                                         usage_bytes = usage_bytes,
                                         last_active_at = datetime.now(timezone.utc))

    @staticmethod
    async def _ingest_single_file(vectorstore_id: str,
                                  api_key: str,
                                  file_id: str,
                                  file_metadata: dict[str, Any],
                                  chunking_strategy: str,
                                  chunk_size: Optional[int],
                                  chunk_overlap: Optional[int],
                                  vector_store_type: str,
                                  trace_context: Optional[TraceCarrier]) -> int:
        """
        Build and run the ingestion pipeline for one file.

        Args:
            vectorstore_id: Target vector store / collection id
            api_key: API key owning the vector store
            file_id: File being ingested
            file_metadata: Row from the files table (filename, bucket, path, ...)
            chunking_strategy: Strategy recorded on the vector store
            chunk_size: Maximum chunk size
            chunk_overlap: Overlap between chunks
            vector_store_type: Backend the vector store was created with
            trace_context: Trace context from the request that queued this task

        Returns:
            int: Number of chunks written to the vector store
        """
        vector_store = VectorStoreFactory.get_store(collection_name = vectorstore_id,
                                                    provider = vector_store_type)

        # Sparse is opt-in and lives on a second model server, so the pipeline
        # is handed the sparse embedder only when this worker actually has one
        pipeline = build_ingestion_pipeline(minio_client = get_minio_service().client,
                                            vector_store = vector_store,
                                            embed_fn = get_dense_embedding,
                                            parsing_service = get_parsing_service(),
                                            chunking_strategy = chunking_strategy,
                                            chunk_size = chunk_size,
                                            chunk_overlap = chunk_overlap,
                                            sparse_embed_fn = (get_sparse_embedding
                                                               if is_sparse_embedding_enabled() else None))

        context = IngestionContext(vector_store_id = vectorstore_id,
                                   api_key = api_key,
                                   file_id = file_id,
                                   file_metadata = file_metadata,
                                   chunking_strategy = chunking_strategy,
                                   chunk_size = chunk_size,
                                   chunk_overlap = chunk_overlap)

        await pipeline.run(context, parent_carrier = trace_context)
        SystemLogger.info(f"[WORKER] Inserted {context.num_inserted} documents into "
                          f"{vector_store_type} collection: {vectorstore_id}")
        return context.num_inserted