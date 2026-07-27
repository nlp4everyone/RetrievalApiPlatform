"""The retrieval pipeline: embed_query -> retrieve -> fuse."""
from typing import Any, Sequence

from app.core.tracing import observation_metadata
from app.pipelines.base import BaseStage
from app.pipelines.pipeline import Pipeline
from app.pipelines.retrieval.context import RetrievalContext


class RetrievalPipeline(Pipeline[RetrievalContext]):
    """Runs the retrieval stages for one query."""

    def __init__(self,
                 stages: Sequence[BaseStage[RetrievalContext]],
                 name: str = "retrieval") -> None:
        super().__init__(stages = stages, name = name)

    def root_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        return observation_metadata(vector_store_id = context.vector_store_id,
                                    limit = context.limit)

    def result_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        # total_candidates lives on the retrieve span; only the final count,
        # which no single stage owns, is recorded here
        return {
            "vector_store.id": context.vector_store_id,
            "retrieval.result_count": len(context.results),
        }