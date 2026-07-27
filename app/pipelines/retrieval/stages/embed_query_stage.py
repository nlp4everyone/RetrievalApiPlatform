"""Stage 1 - turn the query text into a dense vector."""
import json
from typing import Any, Awaitable, Callable, ClassVar, List

from app.core.config import DENSE_MODEL_NAME
from app.core.tracing import OBSERVATION_INPUT, ObservationType
from app.pipelines.retrieval.base import BaseRetrievalStage
from app.pipelines.retrieval.context import RetrievalContext

EmbedFn = Callable[[List[str]], Awaitable[List[List[float]]]]


class EmbedQueryStage(BaseRetrievalStage):
    """Embed the query with the same model used at ingestion time."""

    name: ClassVar[str] = "embed_query"
    observation_type: ClassVar[str] = ObservationType.EMBEDDING

    def __init__(self, embed_fn: EmbedFn) -> None:
        """
        Args:
            embed_fn: Coroutine turning texts into dense vectors
        """
        self._embed_fn = embed_fn

    async def run(self, context: RetrievalContext) -> None:
        """Populate context.dense_vector."""
        vectors = await self._embed_fn([context.query])
        context.dense_vector = vectors[0]

    def span_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        return {
            OBSERVATION_INPUT: json.dumps({"query": context.query}),
            "embedding.model": DENSE_MODEL_NAME,
            "embedding.dims": len(context.dense_vector or []),
        }