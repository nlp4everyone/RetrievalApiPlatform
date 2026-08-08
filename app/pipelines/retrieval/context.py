"""State carried through a retrieval run."""
import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter


def chunks_to_trace_json(chunks: List[RetrievedChunk]) -> Optional[str]:
    """Render hits for a span attribute: identifiers and scores only.

    Chunk text and metadata are deliberately left out - a trace is not the
    place to duplicate document contents, and the ids are enough to look a
    result up when a ranking needs explaining.

    Args:
        chunks: Hits to render, in rank order

    Returns:
        Optional[str]: JSON array of {chunk_id, score}, or None when empty so
            the attribute is dropped rather than set to "[]"
    """
    if not chunks:
        return None
    return json.dumps([{"chunk_id": chunk.id, "score": chunk.score} for chunk in chunks])


@dataclass
class RetrievalContext:
    """Inputs, intermediate results, and metrics for answering one query."""

    # --- Inputs, set by the caller ---------------------------------------
    vector_store_id: str
    api_key: str
    query: str
    limit: int
    filters: Optional[VectorStoreFilter] = None
    score_threshold: Optional[float] = None

    # --- Filled in progressively by the stages ---------------------------
    # Query representations (embed stage). Each retriever consumes the one it
    # understands; sparse stays None unless the search resolved to hybrid.
    dense_vector: Optional[list[float]] = None
    sparse_vector: Optional[dict[int, float]] = None

    # Hits per retriever, keyed by retriever name (retrieve stage). Hybrid
    # search shows up here as more than one entry; fusion collapses them.
    candidates: dict[str, list[RetrievedChunk]] = field(default_factory=dict)

    # Final ranked hits (fuse stage)
    results: list[RetrievedChunk] = field(default_factory=list)

    # Free-form numbers a stage wants on its span but that are not pipeline state
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def total_candidates(self) -> int:
        """How many hits every retriever returned in total, before fusion."""
        return sum(len(hits) for hits in self.candidates.values())
