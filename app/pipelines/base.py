"""The stage contract shared by every pipeline.

Stages contain business logic only. They never open a span, never import the
tracing module, and never touch OpenTelemetry - Pipeline wraps each ``run()``
in a span named after the stage and attaches whatever ``span_attributes()``
reports. Adding a step is adding a class here, not adding tracing code.
"""
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from app.core.tracing import ObservationType

# The context object a given pipeline threads through its stages
ContextT = TypeVar("ContextT")


class BaseStage(ABC, Generic[ContextT]):
    """One step of a pipeline, and one observation in Langfuse."""

    # Span name shown in Langfuse
    name: ClassVar[str]
    # Langfuse observation type; embedding and retrieval steps report themselves
    # as such so Langfuse renders them with the right detail instead of a plain span
    observation_type: ClassVar[str] = ObservationType.SPAN

    @abstractmethod
    async def run(self, context: ContextT) -> None:
        """Do this stage's work, reading from and writing to the context.

        Args:
            context: Shared pipeline state

        Raises:
            Exception: Any failure; the pipeline records it on the span and
                aborts the remaining stages
        """

    def emits_span(self, context: ContextT) -> bool:
        """Whether this stage is worth an observation on this particular run.

        Checked before run(), so it can look at what earlier stages produced.
        Returning False still runs the stage - it only keeps a step that turned
        out to be a no-op from adding noise to the trace.

        Args:
            context: Shared pipeline state as left by the previous stage

        Returns:
            bool: True to open a span for this stage
        """
        return True

    def span_attributes(self, context: ContextT) -> dict[str, Any]:
        """Metrics to attach to this stage's span, read after run() succeeded.

        Not called when emits_span() returned False.

        Args:
            context: Shared pipeline state, now including this stage's output

        Returns:
            dict[str, Any]: Attribute names mapped to values; None values are dropped
        """
        return {}
