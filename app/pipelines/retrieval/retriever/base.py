"""The contract every retriever implements.

A retriever turns one query into a ranked list of chunks. This is the seam
hybrid search would be added at: today the pipeline runs a single dense
retriever, and a keyword/BM25 backend would be a second implementation of this
interface registered alongside it - no change to the stages or the pipeline.

RetrievalQuery deliberately carries the raw query text as well as the dense
vector, so a retriever that does its own tokenising has what it needs without
the pipeline knowing which representation each retriever consumes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, List, Optional

from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter


@dataclass(frozen=True)
class RetrievalQuery:
    """Everything a retriever may need to answer one query."""

    text: str
    limit: int
    dense_vector: Optional[List[float]] = None
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