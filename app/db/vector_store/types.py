"""Backend-neutral types exchanged with any vector store implementation.

Nothing here may import a vendor SDK. These are the only shapes the service
layer sees, so swapping Qdrant for Milvus never reaches above app.db.
"""
from dataclasses import dataclass, field
from typing import Any, Union

from strenum import StrEnum


@dataclass(frozen=True)
class RetrievedChunk:
    """One hit from a similarity search, normalised across backends."""
    id: str
    score: float
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class FilterOperator(StrEnum):
    """Comparison operators a backend filter translator must support."""
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NIN = "nin"


class FilterCombinator(StrEnum):
    """How a FilterGroup joins its children."""
    AND = "and"
    OR = "or"


@dataclass(frozen=True)
class FieldCondition:
    """Compare a single payload/metadata field against a value."""
    key: str
    operator: FilterOperator
    value: Any


@dataclass(frozen=True)
class FilterGroup:
    """Combine nested conditions with AND or OR."""
    combinator: FilterCombinator
    conditions: list[Union["FieldCondition", "FilterGroup"]]


# Either a single condition or a nested group of them
VectorStoreFilter = Union[FieldCondition, FilterGroup]