"""Assemble the ingestion pipeline from configuration.

This is the single place that decides which stages run and in what order, so
adding a step (sparse/BM25 embedding, an OCR pass, a dedup filter) is an edit
here plus one new stage class.
"""
from typing import Optional

from minio import Minio

from app.core.config import EMBEDDING_BATCH_CONCURRENCY, EMBEDDING_UPLOAD_BATCH_SIZE
from app.db.vector_store import BaseAsyncVectorStore
from app.components.chunking import ChunkingService
from app.pipelines.ingestion.pipeline import IngestionPipeline
from app.pipelines.ingestion.stages import (ChunkStage,
                                           DownloadStage,
                                           EmbedAndIndexStage,
                                           ParseStage)
from app.pipelines.ingestion.stages.embed_index_stage import EmbedFn
from app.components.parsing import ParsingService


def build_ingestion_pipeline(minio_client: Minio,
                             vector_store: BaseAsyncVectorStore,
                             embed_fn: EmbedFn,
                             parsing_service: ParsingService,
                             chunking_strategy: str = "auto",
                             chunk_size: Optional[int] = None,
                             chunk_overlap: Optional[int] = None) -> IngestionPipeline:
    """Build the download -> parse -> chunk -> embed+index pipeline.

    Args:
        minio_client: Connected MinIO client for the download stage
        vector_store: Store bound to the target collection
        embed_fn: Coroutine turning texts into dense vectors
        parsing_service: Service resolving a parsing provider per file extension
        chunking_strategy: Strategy recorded on the vector store
        chunk_size: Maximum chunk size
        chunk_overlap: Overlap between chunks

    Returns:
        IngestionPipeline: Pipeline ready to run against an IngestionContext
    """
    # Built per pipeline, not at startup: chunk size and overlap come from the
    # vector store's create request
    chunking_service = ChunkingService.from_settings(chunking_strategy = chunking_strategy,
                                                     chunk_size = chunk_size,
                                                     chunk_overlap = chunk_overlap)

    return IngestionPipeline(stages = [
        DownloadStage(minio_client = minio_client),
        ParseStage(parsing_service = parsing_service),
        ChunkStage(chunking_service = chunking_service),
        EmbedAndIndexStage(vector_store = vector_store,
                           embed_fn = embed_fn,
                           batch_size = EMBEDDING_UPLOAD_BATCH_SIZE,
                           concurrency = EMBEDDING_BATCH_CONCURRENCY),
    ])