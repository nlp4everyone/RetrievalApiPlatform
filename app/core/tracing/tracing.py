"""OpenTelemetry tracing, exported to Langfuse via OTLP.

Call sites use `traced_span()` instead of the raw OTEL API - the exporter
lives only here, so pointing the app at a different OTLP-compatible backend
(MLflow, Grafana Tempo, ...) is a change to this module, not to call sites.
"""
import base64
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
from app.core.tracing.propagation import TraceCarrier, extract_trace_context

_SERVICE_NAME = "retrieval_service"
_tracer: Optional[trace.Tracer] = None


def init_tracing() -> None:
    """Configure the global TracerProvider to export spans to Langfuse via OTLP.

    Idempotent and safe to call from both the web app and the worker process.
    """
    global _tracer
    if _tracer is not None:
        return

    auth = base64.b64encode(f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()).decode()
    provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{LANGFUSE_BASE_URL.rstrip('/')}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {auth}"},
            )
        )
    )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(_SERVICE_NAME)


def _get_tracer() -> trace.Tracer:
    if _tracer is None:
        init_tracing()
    return _tracer


def set_span_attributes(span: Span, attributes: Optional[dict[str, Any]]) -> None:
    """Set several attributes at once, skipping None values.

    OpenTelemetry rejects None attribute values, and callers that report
    optional metrics would otherwise need a guard around every one of them.

    Args:
        span: Span to annotate
        attributes: Attribute names mapped to values; None values are dropped
    """
    if not attributes:
        return
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)


@contextmanager
def traced_span(name: str,
                attributes: Optional[dict[str, Any]] = None,
                parent_carrier: Optional[TraceCarrier] = None) -> Iterator[Span]:
    """Start a span; records exceptions and sets OK/ERROR status on exit.

    Args:
        name: Span name, shown as the observation name in Langfuse
        attributes: Attributes set when the span opens
        parent_carrier: W3C trace context from another process. When given, the
            span joins that trace instead of starting a new one - used by the
            TaskIQ worker to attach ingestion to the HTTP request that queued it.
    """
    parent_context = extract_trace_context(parent_carrier)
    with _get_tracer().start_as_current_span(name,
                                             context=parent_context,
                                             attributes=attributes or {}) as span:
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
