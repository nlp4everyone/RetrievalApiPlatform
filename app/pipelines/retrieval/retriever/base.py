"""The contract every retriever implements.

A retriever turns one query into a ranked list of chunks. This is the seam
hybrid search is added at: HybridRetriever is a second implementation the
factory swaps in, so the stages and the pipeline never learn that a search
consulted two representations instead of one.

RetrievalQuery carries every representation of the query at once (raw text,
dense vector, sparse vector), so each retriever picks the one it understands
without the pipeline knowing which that is.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, List, Optional

from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter


@dataclass(frozen=True)
class RetrievalQuery:
    """Everything a retriever may need to answer one query."""

    text: str
    limit: int
    dense_vector: Optional[List[float]] = None
    sparse_vector: Optional[Dict[int, float]] = None
    filters: Optional[VectorStoreFilter] = None
    score_threshold: Optional[float] = None


class BaseRetriever(ABC):
    """Produces ranked candidate chunks for a query."""

    # Identifies this retriever's hits in RetrievalContext.candidates and on spans
    name: ClassVar[str]

    @abstractmethod
    async def retrieve(self, query: RetrievalQuery) -> List[RetrievedChunk]:
        """Return candidate chunks, best first.

        Args:
            query: The query and the representations available for it

        Returns:
            List[RetrievedChunk]: Hits ranked best first, at most query.limit

        Raises:
            Exception: If the underlying index is unreachable
        """

    def span_attributes(self) -> dict[str, Any]:
        """Metrics from the last retrieve() call, merged onto the retrieve span.

        Keys are prefixed with the retriever name by the stage, so two
        retrievers reporting the same key do not collide.

        Returns:
            dict[str, Any]: Attribute names mapped to values
        """
        return {}