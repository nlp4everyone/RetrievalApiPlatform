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
from typing import Any, ClassVar, List, Optional, Sequence

from langchain_core.documents import Document

from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.schemas.vector_store.types import VectorStoreType

Embedding = List[float]


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
    async def ensure_collection(self, embedding_dim: int) -> bool:
        """Create the collection if it is missing.

        Must be idempotent and must tolerate another process creating the same
        collection concurrently - a backend reporting "already exists" is a
        success, not an error.

        Args:
            embedding_dim: Dimensionality of the vectors that will be stored

        Returns:
            bool: True if this call created the collection, False if it existed
        """

    @abstractmethod
    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Sequence[Embedding],
                               batch_size: int = 16) -> int:
        """Upsert documents and their vectors.

        Assumes ``ensure_collection`` has already run; implementations must not
        create the collection here.

        Args:
            documents: Documents to store, one per embedding
            embeddings: Pre-computed vectors, aligned with documents
            batch_size: Points per round-trip to the backend

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
                       score_threshold: Optional[float] = None) -> List[List[RetrievedChunk]]:
        """Run a similarity search for each query vector.

        Args:
            query_vectors: One vector per query
            limit: Maximum hits per query
            filters: Optional metadata filter, translated by the backend
            score_threshold: Drop hits scoring below this value

        Returns:
            List[List[RetrievedChunk]]: Hits per query, in query order
        """

    @abstractmethod
    async def delete_collection(self) -> bool:
        """Drop the collection.

        Returns:
            bool: True if a collection was deleted, False if it did not exist
        """