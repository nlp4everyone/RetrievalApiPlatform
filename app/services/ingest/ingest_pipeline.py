"""Embedding + Qdrant upsert pipeline used by the vector store ingest task."""
import asyncio

from langchain_core.documents import Document

from app.db.qdrant import AsyncQdrantVectorStore
from app.startup import get_dense_embedding
from app.core.config import DENSE_MODEL_NAME, EMBEDDING_UPLOAD_BATCH_SIZE, EMBEDDING_BATCH_CONCURRENCY
from app.core.tracing import traced_span


async def embed_and_insert_batch(qdrant_vector_store: AsyncQdrantVectorStore,
                                 batch_texts: list,
                                 source_file_id: str,
                                 vectorstore_id: str) -> int:
    """Embed one batch of chunks and upsert it to Qdrant."""
    # Step 1: Embed the batch of chunk texts
    with traced_span(name="embedding",
                     attributes={"langfuse.observation.type": "embedding"}) as embed_span:
        batch_embeddings = await get_dense_embedding(batch_texts)
        embed_span.set_attribute("documents.num_chunks", len(batch_texts))
        embed_span.set_attribute("embedding.model", DENSE_MODEL_NAME)
        embed_span.set_attribute("embedding.dims", len(batch_embeddings[0]))

    # Step 2: Wrap each chunk as a Document, tagged with its source file
    batch_documents = [Document(page_content = text,
                                metadata = {"source": source_file_id}) for text in batch_texts]

    # Step 3: Upsert the embedded documents into Qdrant
    with traced_span(name="upsert",
                     attributes={
                         "langfuse.observation.type": "span",
                         "langfuse.observation.metadata.vector_store_id": vectorstore_id}) as upsert_span:
        upsert_span.set_attribute("vector_store.id", vectorstore_id)
        upsert_span.set_attribute("vector_store.type", "qdrant")
        await qdrant_vector_store.insert_documents(documents = batch_documents,
                                                   embeddings = batch_embeddings,
                                                   upload_batch_size = EMBEDDING_UPLOAD_BATCH_SIZE)
        upsert_span.set_attribute("documents.num_chunks", len(batch_documents))
    return len(batch_documents)


async def embed_and_insert_batch_bounded(semaphore: asyncio.Semaphore,
                                         qdrant_vector_store: AsyncQdrantVectorStore,
                                         batch_texts: list,
                                         source_file_id: str,
                                         vectorstore_id: str) -> int:
    """Same as embed_and_insert_batch, capped by a concurrency semaphore."""
    async with semaphore:
        return await embed_and_insert_batch(qdrant_vector_store, batch_texts, source_file_id, vectorstore_id)


async def embed_and_upload_chunks(qdrant_vector_store: AsyncQdrantVectorStore,
                                  chunked_texts: list,
                                  source_file_id: str,
                                  vectorstore_id: str,
                                  api_key: str) -> int:
    """Embed and upsert chunks to Qdrant in fixed-size batches.

    The first batch runs alone so it creates the collection before concurrent
    batches below could race to create it themselves. Remaining batches run
    concurrently, capped by EMBEDDING_BATCH_CONCURRENCY.
    """
    # Step 1: Split chunks into fixed-size batches
    text_batches = [chunked_texts[i:i + EMBEDDING_UPLOAD_BATCH_SIZE]
                    for i in range(0, len(chunked_texts), EMBEDDING_UPLOAD_BATCH_SIZE)]
    if not text_batches:
        return 0

    with traced_span(name="/v1/vector_stores",
                     attributes={"langfuse.user.id": api_key,
                                 "langfuse.trace.tags": ["vector_store_ingest"],
                                 "langfuse.trace.metadata.source_file_id": source_file_id}) as trace_span:
        # Step 2: Run the first batch alone so it creates the Qdrant collection
        # before any concurrent batch below could race to create it
        total_inserted = await embed_and_insert_batch(qdrant_vector_store, text_batches[0], source_file_id, vectorstore_id)

        # Step 3: Run remaining batches concurrently, capped by EMBEDDING_BATCH_CONCURRENCY
        remaining_batches = text_batches[1:]
        if remaining_batches:
            semaphore = asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY)
            batch_counts = await asyncio.gather(*[
                embed_and_insert_batch_bounded(semaphore, qdrant_vector_store, batch, source_file_id, vectorstore_id)
                for batch in remaining_batches
            ])
            total_inserted += sum(batch_counts)

        # Add attribute
        trace_span.set_attribute("vector_store.upload_batch_size", EMBEDDING_UPLOAD_BATCH_SIZE)
        trace_span.set_attribute("documents.num_chunks", len(chunked_texts))
        trace_span.set_attribute("documents.num_batches", len(text_batches))

    return total_inserted