"""Stage 1 - turn the query text into the vectors the retrievers need."""
import asyncio
import json
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional

from app.core.config import DENSE_MODEL_NAME, SPARSE_MODEL_NAME
from app.core.tracing import OBSERVATION_INPUT, ObservationType
from app.pipelines.retrieval.base import BaseRetrievalStage
from app.pipelines.retrieval.context import RetrievalContext

EmbedFn = Callable[[List[str]], Awaitable[List[List[float]]]]
SparseEmbedFn = Callable[[List[str]], Awaitable[List[Dict[int, float]]]]


class EmbedQueryStage(BaseRetrievalStage):
    """Embed the query with the same model(s) used at ingestion time."""

    name: ClassVar[str] = "embed_query"
    observation_type: ClassVar[str] = ObservationType.EMBEDDING

    def __init__(self,
                 embed_fn: EmbedFn,
                 sparse_embed_fn: Optional[SparseEmbedFn] = None) -> None:
        """
        Args:
            embed_fn: Coroutine turning texts into dense vectors
            sparse_embed_fn: Coroutine turning texts into sparse vectors, supplied
                only for a hybrid search; None leaves context.sparse_vector unset
        """
        self._embed_fn = embed_fn
        self._sparse_embed_fn = sparse_embed_fn

    async def run(self, context: RetrievalContext) -> None:
        """Populate context.dense_vector, and context.sparse_vector when hybrid."""
        if self._sparse_embed_fn is None:
            vectors = await self._embed_fn([context.query])
            context.dense_vector = vectors[0]
            return

        # Two model servers on the query path - run them together so a search
        # waits for the slower one, not for both in turn
        vectors, sparse_vectors = await asyncio.gather(self._embed_fn([context.query]),
                                                       self._sparse_embed_fn([context.query]))
        context.dense_vector = vectors[0]
        context.sparse_vector = sparse_vectors[0]

    def span_attributes(self, context: RetrievalContext) -> dict[str, Any]:
        attributes: dict[str, Any] = {
            OBSERVATION_INPUT: json.dumps({"query": context.query}),
            "embedding.model": DENSE_MODEL_NAME,
            "embedding.dims": len(context.dense_vector or []),
        }
        if self._sparse_embed_fn is not None:
            attributes["embedding.sparse_model"] = SPARSE_MODEL_NAME
            # Number of weighted tokens - a zero here explains a hybrid search
            # that came back looking purely dense
            attributes["embedding.sparse_terms"] = len(context.sparse_vector or {})
        return attributes