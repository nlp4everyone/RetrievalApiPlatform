"""Stage 5 - write chunks and their vectors into the vector store."""
import asyncio
from typing import Any, ClassVar, Sequence

from langchain_core.documents import Document

from app.db.vector_store import BaseAsyncVectorStore
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext


class IndexStage(BaseIngestionStage):
    """Upsert every chunk into the vector store.

    Because the embed stage already produced all vectors, the embedding
    dimension is known before the first write. That lets the collection be
    created once, up front, after which every batch is a pure insert and they
    can all run concurrently - no batch has to go first to win a creation race.
    """

    name: ClassVar[str] = "index"

    def __init__(self,
                 vector_store: BaseAsyncVectorStore,
                 batch_size: int,
                 concurrency: int) -> None:
        """
        Args:
            vector_store: Store bound to this vector store's collection
            batch_size: Documents per upsert request
            concurrency: Upserts allowed in flight at once
        """
        self._vector_store = vector_store
        self._batch_size = batch_size
        self._concurrency = concurrency

    async def run(self, context: IngestionContext) -> None:
        """
        Create the collection if needed, then upsert all chunks.

        Raises:
            ValueError: If chunks and embeddings are not aligned
            Exception: If the vector store rejects a write
        """
        if not context.chunks:
            context.num_inserted = 0
            return
        if len(context.chunks) != len(context.embeddings):
            raise ValueError(f"Number of chunks ({len(context.chunks)}) must equal "
                             f"number of embeddings ({len(context.embeddings)})")

        # One creation, before any concurrent write - the whole reason embed and
        # index are separate stages
        created = await self._vector_store.ensure_collection(
            embedding_dim = len(context.embeddings[0])
        )
        context.metrics["collection_created"] = created

        documents = [Document(page_content = chunk,
                              metadata = {"source": context.file_id})
                     for chunk in context.chunks]

        # Slice documents and vectors together so each batch stays aligned
        batches = [(documents[i:i + self._batch_size], context.embeddings[i:i + self._batch_size])
                   for i in range(0, len(documents), self._batch_size)]
        context.metrics["index_num_batches"] = len(batches)

        semaphore = asyncio.Semaphore(self._concurrency)

        async def upsert_batch(batch_documents: Sequence[Document],
                               batch_embeddings: Sequence[list[float]]) -> int:
            async with semaphore:
                return await self._vector_store.insert_documents(documents = batch_documents,
                                                                 embeddings = batch_embeddings,
                                                                 batch_size = self._batch_size)

        inserted_counts = await asyncio.gather(*[upsert_batch(docs, vectors)
                                                 for docs, vectors in batches])
        context.num_inserted = sum(inserted_counts)

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "vector_store.id": context.vector_store_id,
            "vector_store.type": str(self._vector_store.provider),
            "vector_store.collection_created": context.metrics.get("collection_created"),
            "index.num_documents": context.num_inserted,
            "index.num_batches": context.metrics.get("index_num_batches", 0),
            "index.batch_size": self._batch_size,
            "index.concurrency": self._concurrency,
        }