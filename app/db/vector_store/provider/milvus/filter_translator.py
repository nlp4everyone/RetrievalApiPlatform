"""Translate backend-neutral filters into a Milvus boolean expression.

Milvus takes filters as an expression string rather than a structured model, so
this renders the tree into text like ``metadata["author"] == "x" and
metadata["year"] >= 2020``.
"""
import json
from typing import Any, Optional

from app.db.vector_store.types import (FieldCondition,
                                       FilterCombinator,
                                       FilterOperator,
                                       VectorStoreFilter)

# Documents are stored with their metadata in a "metadata" JSON field (see
# AsyncMilvusVectorStore._to_rows), so a filter on attribute "author" has to
# address the JSON path metadata["author"].
_PAYLOAD_FIELD = "metadata"

# Operators rendered as a plain infix comparison
_COMPARISONS: dict[FilterOperator, str] = {
    FilterOperator.EQ: "==",
    FilterOperator.NE: "!=",
    FilterOperator.GT: ">",
    FilterOperator.GTE: ">=",
    FilterOperator.LT: "<",
    FilterOperator.LTE: "<=",
}

# Operators taking a list on the right-hand side
_MEMBERSHIPS: dict[FilterOperator, str] = {
    FilterOperator.IN: "in",
    FilterOperator.NIN: "not in",
}


def _literal(value: Any) -> str:
    """Render a Python value as a Milvus expression literal.

    JSON encoding is exact here: Milvus spells strings, numbers, booleans and
    null the same way JSON does.

    Args:
        value: Value from a filter condition

    Returns:
        str: The value as expression text
    """
    return json.dumps(value)


def _payload_path(key: str) -> str:
    """Address a user-facing attribute inside the metadata JSON field."""
    return f'{_PAYLOAD_FIELD}[{json.dumps(key)}]'


def _translate_condition(condition: FieldCondition) -> str:
    """Turn one condition into Milvus expression text.

    Args:
        condition: Neutral field condition

    Returns:
        str: Expression fragment

    Raises:
        ValueError: If the operator has no Milvus equivalent
    """
    path = _payload_path(condition.key)

    if condition.operator in _COMPARISONS:
        return f"{path} {_COMPARISONS[condition.operator]} {_literal(condition.value)}"
    if condition.operator in _MEMBERSHIPS:
        values = ", ".join(_literal(item) for item in condition.value)
        return f"{path} {_MEMBERSHIPS[condition.operator]} [{values}]"

    raise ValueError(f"Unsupported filter operator for Milvus: {condition.operator}")


def _translate(node: VectorStoreFilter) -> Optional[str]:
    """Recursively render a filter tree, returning None for an empty group."""
    if isinstance(node, FieldCondition):
        return _translate_condition(node)

    # Groups are parenthesised so nesting binds the way the tree says, not the
    # way Milvus' own operator precedence would.
    joiner = " and " if node.combinator == FilterCombinator.AND else " or "
    children = [rendered for child in node.conditions
                if (rendered := _translate(child)) is not None]
    if not children:
        return None
    return f"({joiner.join(children)})"


def to_milvus_expression(filters: Optional[VectorStoreFilter]) -> Optional[str]:
    """Convert a neutral filter tree into a Milvus boolean expression.

    Args:
        filters: Neutral filter, or None

    Returns:
        Optional[str]: Milvus expression, or None when nothing constrains the search

    Raises:
        ValueError: If the tree contains an operator Milvus cannot express
    """
    if filters is None:
        return None
    return _translate(filters)
