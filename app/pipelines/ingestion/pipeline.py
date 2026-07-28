"""The ingestion pipeline: download -> parse -> chunk -> embed+index."""
from typing import Any, Sequence

from app.core.tracing import observation_metadata
from app.pipelines.base import BaseStage
from app.pipelines.ingestion.context import IngestionContext
from app.pipelines.pipeline import Pipeline


class IngestionPipeline(Pipeline[IngestionContext]):
    """Runs the ingestion stages for one file."""

    def __init__(self,
                 stages: Sequence[BaseStage[IngestionContext]],
                 name: str = "ingestion") -> None:
        super().__init__(stages = stages, name = name)

    def root_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return observation_metadata(vector_store_id = context.vector_store_id,
                                    file_id = context.file_id,
                                    filename = context.filename)

    def result_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "ingestion.num_chunks": len(context.chunks),
            "ingestion.num_inserted": context.num_inserted,
            "vector_store.id": context.vector_store_id,
        }
