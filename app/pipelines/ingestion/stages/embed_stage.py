"""Stage 4 - turn chunks into dense vectors."""
import asyncio
from typing import Any, Awaitable, Callable, ClassVar, Sequence

from app.core.config import DENSE_MODEL_NAME
from app.core.tracing import ObservationType
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext

# Rough per-element cost of a Python float inside a list: the float object plus
# the list's pointer to it. Used only to report peak memory on the span.
_BYTES_PER_FLOAT = 32

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


class EmbedStage(BaseIngestionStage):
    """Embed every chunk, in bounded-concurrency batches.

    All vectors are held in memory until the index stage consumes them, which
    is the cost of separating embedding from indexing. Batching still bounds
    how many requests are in flight; it no longer bounds peak memory, so the
    span reports the estimate to make that visible.
    """

    name: ClassVar[str] = "embed"
    observation_type: ClassVar[str] = ObservationType.EMBEDDING

    def __init__(self,
                 embed_fn: EmbedFn,
                 batch_size: int,
                 concurrency: int) -> None:
        """
        Args:
            embed_fn: Coroutine turning texts into vectors
            batch_size: Chunks per embedding request
            concurrency: Requests allowed in flight at once
        """
        self._embed_fn = embed_fn
        self._batch_size = batch_size
        self._concurrency = concurrency

    async def run(self, context: IngestionContext) -> None:
        """
        Populate context.embeddings, aligned one-to-one with context.chunks.

        Raises:
            Exception: If the embedding backend fails for any batch
        """
        if not context.chunks:
            context.embeddings = []
            return

        batches = [context.chunks[i:i + self._batch_size]
                   for i in range(0, len(context.chunks), self._batch_size)]
        context.metrics["embed_num_batches"] = len(batches)

        semaphore = asyncio.Semaphore(self._concurrency)

        async def embed_batch(batch: Sequence[str]) -> list[list[float]]:
            async with semaphore:
                return await self._embed_fn(list(batch))

        # gather preserves input order, so flattening keeps vectors aligned
        # with their chunks
        batch_results = await asyncio.gather(*[embed_batch(batch) for batch in batches])
        context.embeddings = [vector for batch in batch_results for vector in batch]

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        num_vectors = len(context.embeddings)
        dims = len(context.embeddings[0]) if context.embeddings else 0
        return {
            "embedding.model": DENSE_MODEL_NAME,
            "embedding.dims": dims,
            "embedding.num_chunks": num_vectors,
            "embedding.num_batches": context.metrics.get("embed_num_batches", 0),
            "embedding.batch_size": self._batch_size,
            "embedding.concurrency": self._concurrency,
            # Peak memory held by this stage - watch this on large documents
            "embedding.peak_vectors": num_vectors,
            "embedding.estimated_bytes": num_vectors * dims * _BYTES_PER_FLOAT,
        }