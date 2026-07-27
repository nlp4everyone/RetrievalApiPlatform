"""Stage 2 - run every retriever and collect their candidates."""
import asyncio
from typing import Any, ClassVar, Sequence

from app.core.tracing import ObservationType
from app.pipelines.retrieval.base import BaseRetrievalStage
from app.pipelines.retrieval.context import RetrievalContext, chunks_to_trace_json
from app.pipelines.retrieval.retriever import BaseRetriever, RetrievalQuery


class RetrieveStage(BaseRetrievalStage):
    """Query all configured retrievers concurrently.

    With one retriever this is a plain vector search. With a keyword/BM25
    retriever added it becomes hybrid search without touching this class: the
    retrievers run side by side and each writes its own entry into
    context.candidates for the fuse stage to merge.
    """

    name: ClassVar[str] = "retrieve"
    observation_type: ClassVar[str] = ObservationType.RETRIEVER

    def __init__(self, retrievers: Sequence[BaseRetriever]) -> None:
        """
        Args:
            retrievers: Retrievers to query; must not be empty

        Raises:
            ValueError: If no retriever was given
        """
        if not retrievers:
            raise ValueError("RetrieveStage needs at least one retriever")
        self._retrievers = list(retrievers)

    async def run(self, context: RetrievalContext) -> None:
        """Populate context.candidates, one entry per retriever."""
        query = RetrievalQuery(text = context.query,
                               limit = context.limit,
                               dense_vector = context.dense_vector,
                               filters = context.filters,
                               score_threshold = context.score_threshold)

        results = await asyncio.gather(*[retriever.retrieve(query)
                                         for retriever in self._retrievers])
        context.candidates = {retriever.name: hits
                              for retriever, hits in zip(self._retrievers, results)}

    def span_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        # Candidates from every retriever as one list, in retriever order. With
        # hybrid search this is the pool before fusion, so a chunk found by two
        # retrievers appears twice - fuse.results carries the merged ranking.
        all_candidates = [chunk
                          for retriever in self._retrievers
                          for chunk in context.candidates.get(retriever.name, [])]

        attributes: dict[str, Any] = {
            "vector_store.id": context.vector_store_id,
            "retrieval.retrievers": ",".join(r.name for r in self._retrievers),
            "retrieval.limit": context.limit,
            "retrieval.has_filter": context.filters is not None,
            "retrieval.total_candidates": context.total_candidates,
            "retrieval.results": chunks_to_trace_json(all_candidates),
        }
        # Whatever each retriever reports about itself, namespaced so two
        # retrievers cannot overwrite each other's metrics
        for retriever in self._retrievers:
            for key, value in retriever.span_attributes().items():
                attributes[f"retrieval.{retriever.name}.{key}"] = value
        return attributes