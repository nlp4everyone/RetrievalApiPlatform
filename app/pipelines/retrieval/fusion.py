"""How results from several retrievers are merged into one ranked list.

Only PassthroughFusion exists, because only one retriever runs today. It is
still a real seam rather than inlined code: adding a keyword/BM25 retriever
means writing a fusion strategy here (reciprocal rank fusion, weighted score
fusion, ...) and selecting it in the factory, with no change to the stage that
calls it.
"""
from abc import ABC, abstractmethod
from typing import ClassVar, Dict, List

from app.db.vector_store.types import RetrievedChunk


class BaseFusion(ABC):
    """Merges per-retriever candidate lists into a single ranked list."""

    # Reported on the fuse span so a trace shows which strategy ran
    name: ClassVar[str]

    @abstractmethod
    def fuse(self,
             candidates: Dict[str, List[RetrievedChunk]],
             limit: int) -> List[RetrievedChunk]:
        """Combine candidates into one ranked list.

        Args:
            candidates: Hits per retriever, keyed by retriever name, each
                already ranked best first
            limit: Maximum number of results to return

        Returns:
            List[RetrievedChunk]: Merged hits ranked best first, at most limit
        """


class PassthroughFusion(BaseFusion):
    """Returns the single retriever's hits unchanged.

    Raises if given more than one candidate list rather than silently dropping
    results - if a second retriever has been added, a real fusion strategy has
    to be chosen along with it.
    """

    name: ClassVar[str] = "passthrough"

    def fuse(self,
             candidates: Dict[str, List[RetrievedChunk]],
             limit: int) -> List[RetrievedChunk]:
        """
        Args:
            candidates: Hits from exactly one retriever
            limit: Maximum number of results to return

        Returns:
            List[RetrievedChunk]: That retriever's hits, truncated to limit

        Raises:
            ValueError: If more than one retriever produced candidates
        """
        if len(candidates) > 1:
            raise ValueError(
                f"PassthroughFusion received {len(candidates)} candidate lists "
                f"({', '.join(sorted(candidates))}). Hybrid search needs a fusion "
                f"strategy - implement one in app.pipelines.retrieval.fusion and "
                f"select it in build_retrieval_pipeline()."
            )
        if not candidates:
            return []
        return next(iter(candidates.values()))[:limit]