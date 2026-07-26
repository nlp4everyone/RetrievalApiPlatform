"""Carry an OpenTelemetry trace across a process boundary.

The API enqueues ingestion onto TaskIQ and returns immediately, so the work
happens in a different process. Without propagation, Langfuse shows two
unrelated traces: the HTTP request, and the worker run. Injecting the W3C
``traceparent`` header into the task payload and extracting it in the worker
makes the worker's spans children of the request span, so the whole
upload -> parse -> chunk -> embed -> index flow reads as one trace.
"""
from typing import Optional

from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject

# W3C trace context carrier, e.g. {"traceparent": "00-<trace_id>-<span_id>-01"}
TraceCarrier = dict[str, str]


def inject_trace_context() -> TraceCarrier:
    """Serialise the currently active span into a carrier for a remote process.

    Must be called while a span is active - otherwise the carrier comes back
    empty and the remote side simply starts its own trace.

    Returns:
        TraceCarrier: W3C trace context headers, empty if no span is active
    """
    carrier: TraceCarrier = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: Optional[TraceCarrier]) -> Optional[Context]:
    """Rebuild a parent context from a carrier produced by inject_trace_context.

    Args:
        carrier (Optional[TraceCarrier]): W3C trace context headers, or None

    Returns:
        Optional[Context]: Parent context to start spans under, or None to use
            the ambient context (which is what OpenTelemetry does by default)
    """
    if not carrier:
        return None
    return extract(carrier)