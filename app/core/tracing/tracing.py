"""OpenTelemetry tracing, exported to Langfuse via OTLP.

Call sites use `traced_span()` instead of the raw OTEL API - the exporter
lives only here, so pointing the app at a different OTLP-compatible backend
(MLflow, Grafana Tempo, ...) is a change to this module, not to call sites.
"""
import base64
from contextlib import contextmanager
from typing import Iterator, Optional

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from app.core.config import LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

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


@contextmanager
def traced_span(name: str, attributes: Optional[dict] = None) -> Iterator[Span]:
    """Start a span; records exceptions and sets OK/ERROR status on exit."""
    with _get_tracer().start_as_current_span(name, attributes=attributes or {}) as span:
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
