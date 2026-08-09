"""Hybrid retrieval: dense and sparse consulted together, fused by the backend."""
from typing import Any, ClassVar, List

from app.db.vector_store import BaseAsyncVectorStore
from app.db.vector_store.types import RetrievedChunk
from app.pipelines.retrieval.retriever.base import BaseRetriever, RetrievalQuery


class HybridRetriever(BaseRetriever):
    """Dense + sparse search over one collection, merged by reciprocal rank fusion.

    This is one retriever rather than two because the fusion happens inside the
    vector store: a single query carries both representations, the backend ranks
    each branch and merges them, and one ranked list comes back. That keeps it
    to one round-trip, and means the two score scales - cosine and term-weight
    dot product - never have to be made comparable, only their ranks.

    A query that produced no sparse vector degrades to dense search rather than
    failing: the point of hybrid is more recall, so losing the lexical half is
    worth less than losing the answer.
    """

    name: ClassVar[str] = "hybrid"

    def __init__(self, vector_store: BaseAsyncVectorStore) -> None:
        """
        Args:
            vector_store: Store bound to the collection being searched
        """
        self._vector_store = vector_store
        self._collection_exists: bool = False
        self._used_sparse: bool = False

    async def retrieve(self, query: RetrievalQuery) -> List[RetrievedChunk]:
        """
        Search the collection with the query's dense and sparse vectors at once.

        Like the dense retriever, a missing collection is an empty result rather
        than an error - a vector store row can exist in Postgres before
        ingestion has created the collection.

        Args:
            query: Query carrying the dense vector, and ideally the sparse one

        Returns:
            List[RetrievedChunk]: Fused hits ranked best first

        Raises:
            ValueError: If no dense vector was computed for the query
        """
        if query.dense_vector is None:
            raise ValueError("HybridRetriever requires a dense query vector")

        self._collection_exists = await self._vector_store.collection_exists()
        if not self._collection_exists:
            return []

        self._used_sparse = bool(query.sparse_vector)
        hits = await self._vector_store.retrieve(
            query_vectors = [query.dense_vector],
            sparse_query_vectors = [query.sparse_vector] if self._used_sparse else None,
            limit = query.limit,
            filters = query.filters,
            score_threshold = query.score_threshold)
        return hits[0] if hits else []

    def span_attributes(self) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            "vector_store.type": str(self._vector_store.provider),
            "collection_exists": self._collection_exists,
            # False here means the search silently ran dense-only, which is the
            # first thing to check when hybrid results look like dense ones
            "used_sparse": self._used_sparse,
        }
        # Fusion settings, when the backend exposes them, so a ranking can be read
        # against what produced it. The stage namespaces these under retrieval.hybrid.*
        for span_key, store_attr in (("prefetch_multiplier", "hybrid_prefetch_multiplier"),
                                     ("rrf_k", "rrf_k")):
            value = getattr(self._vector_store, store_attr, None)
            if value is not None:
                attributes[span_key] = value
        return attributes