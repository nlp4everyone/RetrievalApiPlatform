"""Stage 4 - embed each batch of chunks and upsert it immediately.

Merges what used to be two stages (embed every chunk, then index every chunk)
into one streaming loop: each batch is embedded and upserted in turn, bounded
by a single semaphore, so at most `concurrency` batches' worth of chunks,
vectors, and Document copies are ever held in memory at once - not the whole
file's.
"""
import asyncio
import time
from typing import Any, Awaitable, Callable, ClassVar, Optional, Sequence

from langchain_core.documents import Document

from app.core.config import DENSE_MODEL_NAME, SPARSE_MODEL_NAME
from app.db.vector_store import BaseAsyncVectorStore
from app.db.vector_store.base import SparseEmbedding
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.startup import get_dense_embedding_dim

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
SparseEmbedFn = Callable[[list[str]], Awaitable[list[SparseEmbedding]]]


def _union_duration(intervals: list[tuple[float, float]]) -> float:
    """Wall-clock time actually covered by a set of (start, end) intervals.

    Batches run concurrently under the semaphore, so summing their raw
    durations would double-count overlapping work; merging overlaps first
    gives the real elapsed time instead.
    """
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    cur_start, cur_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


class EmbedAndIndexStage(BaseIngestionStage):
    """Embed and upsert every chunk, one batch at a time.

    The collection is created up front from the embedding model's known
    dimension (app.startup.get_dense_embedding_dim) instead of from the first
    embedding result - that's what makes streaming possible instead of
    embedding the whole file before any write can start.

    With a sparse_embed_fn supplied, each batch is embedded twice - dense and
    sparse concurrently - and both vectors ride on the same point, so hybrid
    retrieval needs no second pass over the file.
    """

    name: ClassVar[str] = "embed_index"

    def __init__(self,
                 vector_store: BaseAsyncVectorStore,
                 embed_fn: EmbedFn,
                 batch_size: int,
                 concurrency: int,
                 sparse_embed_fn: Optional[SparseEmbedFn] = None) -> None:
        """
        Args:
            vector_store: Store bound to this vector store's collection
            embed_fn: Coroutine turning texts into dense vectors
            batch_size: Chunks per embed request and per upsert request
            concurrency: Batches allowed in flight at once (embed + upsert combined)
            sparse_embed_fn: Optional coroutine turning texts into sparse vectors.
                None (the default) keeps ingestion dense-only
        """
        self._vector_store = vector_store
        self._embed_fn = embed_fn
        self._batch_size = batch_size
        self._concurrency = concurrency
        self._sparse_embed_fn = sparse_embed_fn

    async def run(self, context: IngestionContext) -> None:
        """
        Create the collection, then embed and upsert each batch of chunks in turn.

        Raises:
            Exception: If the embedding backend or the vector store fails for any batch
        """
        if not context.chunks:
            context.num_inserted = 0
            return

        embedding_dim = await get_dense_embedding_dim()
        want_sparse = self._sparse_embed_fn is not None
        created = await self._vector_store.ensure_collection(embedding_dim = embedding_dim,
                                                             with_sparse = want_sparse)
        # A collection created before sparse was switched on has no sparse field
        # and Qdrant cannot add one, so ask the collection rather than trusting
        # config - writing a sparse vector it cannot hold would fail every batch
        use_sparse = want_sparse and await self._vector_store.supports_sparse()
        context.metrics["collection_created"] = created
        context.metrics["embedding_dim"] = embedding_dim
        context.metrics["sparse_enabled"] = use_sparse

        batches = [context.chunks[i:i + self._batch_size]
                   for i in range(0, len(context.chunks), self._batch_size)]
        context.metrics["num_batches"] = len(batches)

        semaphore = asyncio.Semaphore(self._concurrency)
        embed_intervals: list[tuple[float, float]] = []
        index_intervals: list[tuple[float, float]] = []

        async def process_batch(batch_chunks: Sequence[str]) -> int:
            async with semaphore:
                texts = list(batch_chunks)
                t0 = time.monotonic()
                if use_sparse:
                    # Two independent model servers - run them together so the
                    # batch costs the slower of the two, not their sum
                    vectors, sparse_vectors = await asyncio.gather(self._embed_fn(texts),
                                                                   self._sparse_embed_fn(texts))
                else:
                    vectors, sparse_vectors = await self._embed_fn(texts), None
                embed_intervals.append((t0, time.monotonic()))

                # Built only now, per batch, so at most `concurrency` batches'
                # worth of Document copies exist at once instead of the whole file's
                batch_documents = [Document(page_content = chunk,
                                            metadata = {"source": context.file_id})
                                  for chunk in batch_chunks]

                t0 = time.monotonic()
                inserted = await self._vector_store.insert_documents(documents = batch_documents,
                                                                     embeddings = vectors,
                                                                     batch_size = self._batch_size,
                                                                     sparse_embeddings = sparse_vectors)
                index_intervals.append((t0, time.monotonic()))
                return inserted

        inserted_counts = await asyncio.gather(*[process_batch(batch) for batch in batches])
        context.num_inserted = sum(inserted_counts)
        context.metrics["embed_wall_clock_s"] = round(_union_duration(embed_intervals), 5)
        context.metrics["index_wall_clock_s"] = round(_union_duration(index_intervals), 5)

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "vector_store.id": context.vector_store_id,
            "vector_store.type": str(self._vector_store.provider),
            "vector_store.collection_created": context.metrics.get("collection_created"),
            "embedding.model": DENSE_MODEL_NAME,
            "embedding.dims": context.metrics.get("embedding_dim"),
            "embedding.sparse_enabled": context.metrics.get("sparse_enabled"),
            "embedding.sparse_model": SPARSE_MODEL_NAME if context.metrics.get("sparse_enabled") else None,
            "embedding.wall_clock_s": context.metrics.get("embed_wall_clock_s"),
            "index.wall_clock_s": context.metrics.get("index_wall_clock_s"),
            "index.num_documents": context.num_inserted,
            "batch.num_batches": context.metrics.get("num_batches", 0),
            "batch.size": self._batch_size,
            "batch.concurrency": self._concurrency,
        }
