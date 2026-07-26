"""Translate backend-neutral filters into a Milvus boolean expression.

Not implemented yet. Milvus filters are expression strings rather than a
structured model, so this should render VectorStoreFilter into something like
``metadata["author"] == "x" && metadata["year"] >= 2020``.
"""
from typing import Optional

from app.db.vector_store.types import VectorStoreFilter


def to_milvus_expression(filters: Optional[VectorStoreFilter]) -> Optional[str]:
    """Convert a neutral filter tree into a Milvus boolean expression.

    Args:
        filters: Neutral filter, or None

    Returns:
        Optional[str]: Milvus expression, or None when no filter was given

    Raises:
        NotImplementedError: Always, until Milvus support lands
    """
    if filters is None:
        return None
    raise NotImplementedError("Milvus filter translation is not implemented yet")