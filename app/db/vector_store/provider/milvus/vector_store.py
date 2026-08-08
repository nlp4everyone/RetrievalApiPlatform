"""Milvus vector store - not implemented yet.

Every method raises NotImplementedError. The class exists so the provider is
already wired end to end (config validation, VectorStoreType, VectorStoreFactory,
startup), which means enabling Milvus later is filling in these bodies plus
adding pymilvus to pyproject.toml - no changes above app.db.

Implementation notes for whoever picks this up:

* ``ensure_collection`` must tolerate a concurrent create the same way
  ``AsyncQdrantVectorStore.ensure_collection`` does: on failure, re-check
  existence and treat "already there" as success.
* ``retrieve`` must return ``RetrievedChunk`` objects, never raw Milvus hits -
  the service layer is not allowed to see backend types.
* Documents are stored with ``page_content`` and ``metadata`` fields to stay
  payload-compatible with the Qdrant backend.
* ``supports_sparse`` must report what the *live* collection holds, not what
  config asks for: sparse embedding can be enabled after a collection was
  created, and ingestion skips writing sparse vectors when it reads False.
* ``retrieve`` with ``sparse_query_vectors`` must fuse the dense and sparse
  branches by rank inside the backend, as the Qdrant store does with an RRF
  prefetch - the caller gets one ranked list, never two to merge itself.
"""
from typing import ClassVar, List, Optional, Sequence

from langchain_core.documents import Document

from app.db.vector_store.base import BaseAsyncVectorStore, Embedding, SparseEmbedding
from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.schemas.vector_store.types import VectorStoreType

_NOT_IMPLEMENTED = "Milvus vector store is not implemented yet"


class AsyncMilvusVectorStore(BaseAsyncVectorStore):
    """Placeholder implementation of the vector store contract for Milvus."""

    provider: ClassVar[VectorStoreType] = VectorStoreType.MILVUS

    def __init__(self,
                 collection_name: str,
                 client: object) -> None:
        """
        Args:
            collection_name: Collection to operate on.
            client: Connected Milvus client supplied by MilvusConnection.
        """
        self._collection_name = collection_name
        self._client = client

    @property
    def collection_name(self) -> str:
        return self._collection_name

    async def collection_exists(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def ensure_collection(self,
                                embedding_dim: int,
                                with_sparse: bool = False) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def supports_sparse(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Sequence[Embedding],
                               batch_size: int = 16,
                               sparse_embeddings: Optional[Sequence[SparseEmbedding]] = None) -> int:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def retrieve(self,
                       query_vectors: Sequence[Embedding],
                       limit: int = 10,
                       filters: Optional[VectorStoreFilter] = None,
                       score_threshold: Optional[float] = None,
                       sparse_query_vectors: Optional[Sequence[SparseEmbedding]] = None) -> List[List[RetrievedChunk]]:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def delete_collection(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED)