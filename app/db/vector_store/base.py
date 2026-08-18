"""The contract every vector store backend implements.

Two abstractions live here:

* ``BaseVectorStoreConnection`` - a long-lived connection created once at startup
  (the Qdrant/Milvus equivalent of a connection pool).
* ``BaseAsyncVectorStore`` - operations scoped to one collection, constructed
  per request from a client.

Note that ``ensure_collection`` is deliberately separate from
``insert_documents``. Folding collection creation into the insert path forces
every concurrent batch to race on a check-then-act, which is why the previous
ingest code had to run its first batch alone. Callers now create the collection
once up front, after which inserts are pure and safely parallel.
"""
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Sequence

from langchain_core.documents import Document

from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.schemas.vector_store.types import VectorStoreType

Embedding = List[float]
# Sparse vectors are {token_id: weight}; only the tokens the model gave weight
# to are carried, so this never has a fixed length the way Embedding does
SparseEmbedding = Dict[int, float]


# ------------------------------------------------------------------
# SHARED VALIDATION
# ------------------------------------------------------------------
# Length checks common to insert_documents/retrieve, called by every backend
# implementation so Qdrant and Milvus don't each carry their own copy.
def validate_insert_lengths(documents: Sequence[Document],
                            embeddings: Sequence[Embedding],
                            sparse_embeddings: Optional[Sequence[SparseEmbedding]] = None) -> None:
    """Validate insert_documents inputs shared by every backend.

    Args:
        documents: Documents to store, one per embedding.
        embeddings: Dense vectors, aligned with documents.
        sparse_embeddings: Optional sparse vectors, aligned with documents.

    Raises:
        ValueError: If documents is empty or lengths do not match.
    """
    if not documents:
        raise ValueError("Documents list is empty")
    if len(documents) != len(embeddings):
        raise ValueError(f"Number of documents ({len(documents)}) must equal "
                         f"number of embeddings ({len(embeddings)})")
    if sparse_embeddings is not None and len(documents) != len(sparse_embeddings):
        raise ValueError(f"Number of documents ({len(documents)}) must equal "
                         f"number of sparse embeddings ({len(sparse_embeddings)})")


def validate_retrieve_lengths(query_vectors: Sequence[Embedding],
                              sparse_query_vectors: Optional[Sequence[SparseEmbedding]] = None) -> None:
    """Validate retrieve inputs shared by every backend.

    Args:
        query_vectors: One dense vector per query.
        sparse_query_vectors: Optional sparse vector per query, aligned with query_vectors.

    Raises:
        ValueError: If sparse vectors are given but do not match the dense ones in number.
    """
    if sparse_query_vectors is not None and len(sparse_query_vectors) != len(query_vectors):
        raise ValueError(f"Number of dense query vectors ({len(query_vectors)}) must equal "
                         f"number of sparse query vectors ({len(sparse_query_vectors)})")


class BaseVectorStoreConnection(ABC):
    """A connection to a vector database, created once at application startup."""

    provider: ClassVar[VectorStoreType]

    @classmethod
    @abstractmethod
    def from_settings(cls) -> "BaseVectorStoreConnection":
        """Build the client from application config.

        Mirrors EmbeddingService.from_settings(): startup never has to know
        which connection parameters a given backend needs.

        Returns:
            BaseVectorStoreConnection: Configured, not yet verified, client
        """

    @property
    @abstractmethod
    def client(self) -> Any:
        """The underlying vendor client, used only by this backend's own code."""

    @abstractmethod
    async def check_connection(self) -> None:
        """Probe the server.

        Raises:
            Exception: If the vector database is unreachable
        """

    @abstractmethod
    async def close(self) -> None:
        """Release the connection. Safe to call when already closed."""


class BaseAsyncVectorStore(ABC):
    """Operations against a single collection in a vector database."""

    provider: ClassVar[VectorStoreType]

    @property
    @abstractmethod
    def collection_name(self) -> str:
        """Name of the collection this instance is bound to."""

    @abstractmethod
    async def collection_exists(self) -> bool:
        """Whether the collection currently exists.

        Returns:
            bool: True if the backend already holds this collection
        """

    @abstractmethod
    async def ensure_collection(self,
                                embedding_dim: int,
                                with_sparse: bool = False) -> bool:
        """Create the collection if it is missing.

        Must be idempotent and must tolerate another process creating the same
        collection concurrently - a backend reporting "already exists" is a
        success, not an error.

        Args:
            embedding_dim: Dimensionality of the vectors that will be stored
            with_sparse: Also give the collection a sparse vector field, so
                documents can carry a dense and a sparse vector side by side.
                Only honoured when this call creates the collection - an
                existing one keeps whatever schema it was created with, which
                is what ``supports_sparse`` is for

        Returns:
            bool: True if this call created the collection, False if it existed
        """

    @abstractmethod
    async def supports_sparse(self, collection_exists: Optional[bool] = None) -> bool:
        """Whether this collection can store sparse vectors.

        A collection created before sparse embedding was switched on has no
        sparse field, and writing one into it fails. Callers therefore ask the
        live collection rather than assuming their own config applies to it.

        Args:
            collection_exists: Pass the result of an already-made
                collection_exists() call to skip repeating it - the search path
                already knows this by the time it asks about sparse support.
                None (the default) makes this call collection_exists() itself.

        Returns:
            bool: True if the collection exists and holds a sparse vector field
        """

    @abstractmethod
    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Sequence[Embedding],
                               batch_size: int = 16,
                               sparse_embeddings: Optional[Sequence[SparseEmbedding]] = None) -> int:
        """Upsert documents and their vectors.

        Assumes ``ensure_collection`` has already run; implementations must not
        create the collection here.

        Args:
            documents: Documents to store, one per embedding
            embeddings: Pre-computed dense vectors, aligned with documents
            batch_size: Points per round-trip to the backend
            sparse_embeddings: Optional sparse vectors, aligned with documents.
                Each point then carries both representations. Requires a
                collection created with ``with_sparse``

        Returns:
            int: Number of documents written

        Raises:
            ValueError: If documents is empty or lengths do not match
        """

    @abstractmethod
    async def retrieve(self,
                       query_vectors: Sequence[Embedding],
                       limit: int = 10,
                       filters: Optional[VectorStoreFilter] = None,
                       score_threshold: Optional[float] = None,
                       sparse_query_vectors: Optional[Sequence[SparseEmbedding]] = None) -> List[List[RetrievedChunk]]:
        """Run a similarity search for each query vector.

        Passing sparse vectors as well makes this one hybrid search rather than
        two searches: the backend queries both fields and merges them by rank
        (reciprocal rank fusion), which it can do in a single round-trip and
        without the two score scales - cosine and term-weight dot product -
        ever having to be made comparable.

        Args:
            query_vectors: One dense vector per query
            limit: Maximum hits per query
            filters: Optional metadata filter, translated by the backend
            score_threshold: Drop hits scoring below this value. Applies to the
                dense scores only, since it is expressed in their scale
            sparse_query_vectors: Optional {token_id: weight} mapping per query,
                aligned with query_vectors. Requires a collection holding a
                sparse field (see supports_sparse)

        Returns:
            List[List[RetrievedChunk]]: Hits per query, in query order. Scores
                are fusion scores, not similarities, when sparse vectors are given
        """

    @abstractmethod
    async def delete_collection(self) -> bool:
        """Drop the collection.

        Returns:
            bool: True if a collection was deleted, False if it did not exist
        """