"""Langfuse attribute names, kept in one place instead of typed as literals.

Langfuse reads two disjoint families of OpenTelemetry attributes:

* ``langfuse.trace.*`` / ``langfuse.user.id`` / ``langfuse.session.id`` are
  read **only from the root span of a trace**. Setting them on a child span
  has no effect.
* ``langfuse.observation.*`` applies to whichever span carries them.

That distinction matters here because ingestion runs in the TaskIQ worker as a
child of the HTTP request span (see ``app.core.tracing.propagation``), so the
trace-level attributes belong to the API process and the worker only ever sets
observation-level ones.
"""
from typing import Any, Final


# --- Trace level (root span only) ------------------------------------------
TRACE_NAME: Final[str] = "langfuse.trace.name"
TRACE_USER_ID: Final[str] = "langfuse.user.id"
TRACE_SESSION_ID: Final[str] = "langfuse.session.id"
TRACE_TAGS: Final[str] = "langfuse.trace.tags"
TRACE_INPUT: Final[str] = "langfuse.trace.input"
TRACE_OUTPUT: Final[str] = "langfuse.trace.output"
_TRACE_METADATA_PREFIX: Final[str] = "langfuse.trace.metadata."

# --- Observation level (any span) ------------------------------------------
OBSERVATION_TYPE: Final[str] = "langfuse.observation.type"
OBSERVATION_INPUT: Final[str] = "langfuse.observation.input"
OBSERVATION_OUTPUT: Final[str] = "langfuse.observation.output"
_OBSERVATION_METADATA_PREFIX: Final[str] = "langfuse.observation.metadata."


class ObservationType:
    """Values accepted by ``langfuse.observation.type``."""
    SPAN: Final[str] = "span"
    EMBEDDING: Final[str] = "embedding"
    RETRIEVER: Final[str] = "retriever"
    GENERATION: Final[str] = "generation"


def trace_metadata(**fields: Any) -> dict[str, Any]:
    """Build ``langfuse.trace.metadata.*`` attributes, dropping None values.

    Args:
        **fields: Metadata keys and values to attach to the trace

    Returns:
        dict[str, Any]: Prefixed attribute names mapped to their values
    """
    return {f"{_TRACE_METADATA_PREFIX}{key}": value
            for key, value in fields.items() if value is not None}


def observation_metadata(**fields: Any) -> dict[str, Any]:
    """Build ``langfuse.observation.metadata.*`` attributes, dropping None values.

    Args:
        **fields: Metadata keys and values to attach to the observation

    Returns:
        dict[str, Any]: Prefixed attribute names mapped to their values
    """
    return {f"{_OBSERVATION_METADATA_PREFIX}{key}": value
            for key, value in fields.items() if value is not None}