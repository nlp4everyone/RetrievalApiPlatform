"""Translate backend-neutral filters into Qdrant's filter model."""
from typing import Any, Optional

from qdrant_client import models

from app.db.vector_store.types import (FieldCondition,
                                       FilterCombinator,
                                       FilterOperator,
                                       VectorStoreFilter)

# Documents are stored with their metadata under a "metadata" payload key
# (see AsyncQdrantVectorStore._convert_documents_to_payloads), so a filter on
# attribute "author" has to address payload field "metadata.author".
_PAYLOAD_PREFIX = "metadata"

# Operators that map onto a Qdrant Range
_RANGE_OPERATORS: dict[FilterOperator, str] = {
    FilterOperator.GT: "gt",
    FilterOperator.GTE: "gte",
    FilterOperator.LT: "lt",
    FilterOperator.LTE: "lte",
}


def _payload_key(key: str) -> str:
    """Qualify a user-facing attribute name with the payload prefix."""
    return f"{_PAYLOAD_PREFIX}.{key}"


def _translate_condition(condition: FieldCondition) -> tuple[str, Any]:
    """Turn one condition into a Qdrant condition plus the clause it belongs in.

    Args:
        condition: Neutral field condition

    Returns:
        tuple[str, Any]: Clause name ("must" or "must_not") and the condition

    Raises:
        ValueError: If the operator has no Qdrant equivalent
    """
    key = _payload_key(condition.key)

    if condition.operator == FilterOperator.EQ:
        return "must", models.FieldCondition(key=key,
                                             match=models.MatchValue(value=condition.value))
    if condition.operator == FilterOperator.NE:
        return "must_not", models.FieldCondition(key=key,
                                                 match=models.MatchValue(value=condition.value))
    if condition.operator == FilterOperator.IN:
        return "must", models.FieldCondition(key=key,
                                             match=models.MatchAny(any=list(condition.value)))
    if condition.operator == FilterOperator.NIN:
        return "must", models.FieldCondition(key=key,
                                             match=models.MatchExcept(**{"except": list(condition.value)}))
    if condition.operator in _RANGE_OPERATORS:
        bound = _RANGE_OPERATORS[condition.operator]
        return "must", models.FieldCondition(key=key,
                                             range=models.Range(**{bound: condition.value}))

    raise ValueError(f"Unsupported filter operator for Qdrant: {condition.operator}")


def _translate(node: VectorStoreFilter) -> models.Filter:
    """Recursively translate a filter tree into a Qdrant Filter."""
    if isinstance(node, FieldCondition):
        clause, condition = _translate_condition(node)
        return models.Filter(**{clause: [condition]})

    # FilterGroup: AND puts children in "must", OR puts them in "should".
    # Nested groups become nested Filters, which Qdrant accepts as conditions.
    children = [_translate(child) for child in node.conditions]
    if node.combinator == FilterCombinator.AND:
        return models.Filter(must=children)
    return models.Filter(should=children)


def to_qdrant_filter(filters: Optional[VectorStoreFilter]) -> Optional[models.Filter]:
    """Convert a neutral filter tree into a Qdrant Filter.

    Args:
        filters: Neutral filter, or None

    Returns:
        Optional[models.Filter]: Qdrant filter, or None when no filter was given

    Raises:
        ValueError: If the tree contains an operator Qdrant cannot express
    """
    if filters is None:
        return None
    return _translate(filters)