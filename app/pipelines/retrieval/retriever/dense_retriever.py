"""Dense vector similarity retrieval, backed by the configured vector store."""
from typing import Any, ClassVar, List, Optional

from app.db.vector_store import BaseAsyncVectorStore
from app.db.vector_store.types import RetrievedChunk
from app.pipelines.retrieval.retriever.base import BaseRetriever, RetrievalQuery


class DenseRetriever(BaseRetriever):
    """Nearest-neighbour search over the vector store collection."""

    name: ClassVar[str] = "dense"

    def __init__(self,
                vector_store: BaseAsyncVectorStore,
                known_collection_exists: Optional[bool] = None) -> None:
        """
        Args:
            vector_store: Store bound to the collection being searched
            known_collection_exists: Pass an already-known collection_exists()
                result (e.g. from resolving the search type) to skip repeating
                that round-trip here. None (the default) checks it fresh.
        """
        self._vector_store = vector_store
        self._known_collection_exists = known_collection_exists
        self._collection_exists: bool = False

    async def retrieve(self, query: RetrievalQuery) -> List[RetrievedChunk]:
        """
        Search the collection for the query's dense vector.

        A vector store row can exist in Postgres before ingestion has created
        the collection, so a missing collection is an empty result rather than
        an error - the caller asked a valid store that has nothing in it yet.

        Args:
            query: Query carrying the dense vector to search with

        Returns:
            List[RetrievedChunk]: Hits ranked best first

        Raises:
            ValueError: If no dense vector was computed for the query
        """
        if query.dense_vector is None:
            raise ValueError("DenseRetriever requires a dense query vector")

        self._collection_exists = (self._known_collection_exists
                                   if self._known_collection_exists is not None
                                   else await self._vector_store.collection_exists())
        if not self._collection_exists:
            return []

        hits = await self._vector_store.retrieve(query_vectors = [query.dense_vector],
                                                 limit = query.limit,
                                                 filters = query.filters,
                                                 score_threshold = query.score_threshold)
        return hits[0] if hits else []

    def span_attributes(self) -> dict[str, Any]:
        return {
            "vector_store.type": str(self._vector_store.provider),
            "collection_exists": self._collection_exists,
        }