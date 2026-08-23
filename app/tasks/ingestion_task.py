"""The one background task: ingest the file(s) behind a vector store.

Orchestration only: bind the request's correlation id onto this task's
context and delegate to app.services.ingestion.IngestionService. All the
actual work - fetching file metadata, running the ingestion pipeline,
recording the outcome - lives there, not here.
"""
from typing import Optional

from app.core.request_context import request_id_ctx
from app.core.tracing import TraceCarrier
from app.schemas.vector_store.types import VectorStoreType
from app.services.ingestion import IngestionService
from loggers import SystemLogger

from .broker import broker


@broker.task
async def ingest_vector_store_files(vectorstore_id: str,
                                    api_key: str,
                                    file_ids: list[str],
                                    chunking_strategy: str,
                                    chunking_splitter: Optional[str] = None,
                                    chunk_size: Optional[int] = None,
                                    chunk_overlap: Optional[int] = None,
                                    request_id: str = "-",
                                    trace_context: Optional[TraceCarrier] = None,
                                    vector_store_type: str = VectorStoreType.QDRANT) -> None:
    """
    Background task to ingest the file(s) behind a vector store.

    Args:
        vectorstore_id (str): Unique identifier for the vector store
        api_key (str): API key for authentication and authorization
        file_ids (list[str]): List of file IDs to process
        chunking_strategy (str): Strategy for text chunking ("auto" or "static")
        chunking_splitter (Optional[str]): Splitter the request asked for
                          ("recursive", "markdown", ...); None means detect it
                          from the parsed document
        chunk_size (Optional[int]): Size of text chunks for static strategy
        chunk_overlap (Optional[int]): Overlap between chunks for static strategy
        request_id (str): ID of the HTTP request that enqueued this task, so
                          worker logs can be correlated with the originating request
        trace_context (Optional[TraceCarrier]): W3C trace context from that same
                          request, so ingestion spans land inside its Langfuse trace
        vector_store_type (str): Backend the vector store was created with
    """
    token = request_id_ctx.set(request_id)
    try:
        await IngestionService.ingest_vector_store_files(vectorstore_id = vectorstore_id,
                                                          api_key = api_key,
                                                          file_ids = file_ids,
                                                          chunking_strategy = chunking_strategy,
                                                          chunking_splitter = chunking_splitter,
                                                          chunk_size = chunk_size,
                                                          chunk_overlap = chunk_overlap,
                                                          vector_store_type = vector_store_type,
                                                          trace_context = trace_context)
    except Exception as e:
        SystemLogger.error(f"[WORKER] Error in ingest_vector_store_files: {str(e)}")
        raise  # Re-raise to allow TaskIQ to handle the failure
    finally:
        request_id_ctx.reset(token)