"""Stage 3 - merge the retrievers' candidates into one ranked list."""
from typing import Any, ClassVar

from app.pipelines.retrieval.base import BaseRetrievalStage
from app.pipelines.retrieval.context import RetrievalContext, chunks_to_trace_json
from app.pipelines.retrieval.fusion import BaseFusion


class FuseStage(BaseRetrievalStage):
    """Apply the configured fusion strategy to the candidate lists.

    A no-op while one retriever runs. It exists as its own stage so that
    enabling hybrid search is a change of strategy in the factory rather than
    new branching inside the retrieve stage.
    """

    name: ClassVar[str] = "fuse"

    def __init__(self, fusion: BaseFusion) -> None:
        """
        Args:
            fusion: Strategy merging per-retriever candidates
        """
        self._fusion = fusion

    def emits_span(self, context: RetrievalContext) -> bool:
        """
        Only worth an observation when there was actually something to merge.

        A dense-only search produces one candidate list, so fusion is a no-op
        and its span would be pure noise on every trace. The span comes back on
        its own once hybrid search puts a second list in context.candidates.
        """
        return len(context.candidates) > 1

    async def run(self, context: RetrievalContext) -> None:
        """Populate context.results."""
        context.results = self._fusion.fuse(candidates = context.candidates,
                                            limit = context.limit)

    def span_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        return {
            "fuse.strategy": self._fusion.name,
            "fuse.num_candidate_lists": len(context.candidates),
            "fuse.result_count": len(context.results),
            # The merged ranking, to compare against each retriever's own list
            # on the retrieve span
            "fuse.results": chunks_to_trace_json(context.results),
        }