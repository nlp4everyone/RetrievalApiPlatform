"""Runs stages in order and is the only place that opens spans.

Concentrating tracing here means the trace shape is a property of the pipeline,
not something each stage has to remember to get right - and it stays correct
when stages are added, removed, or reordered.
"""
from contextlib import nullcontext
from typing import Any, Generic, Optional, Sequence

from app.core.tracing import (OBSERVATION_TYPE,
                              ObservationType,
                              TraceCarrier,
                              set_span_attributes,
                              traced_span)
from app.pipelines.base import BaseStage, ContextT
from loggers import SystemLogger


class Pipeline(Generic[ContextT]):
    """Executes a fixed sequence of stages against one context object.

    Subclasses describe what to record about the run as a whole by overriding
    root_attributes() and result_attributes(); everything else - span nesting,
    error handling, per-stage attributes - is the same for every pipeline.
    """

    def __init__(self,
                 stages: Sequence[BaseStage[ContextT]],
                 name: str) -> None:
        """
        Args:
            stages: Stages to run, in order
            name: Name of the span wrapping the whole run
        """
        self._stages = list(stages)
        self._name = name

    @property
    def name(self) -> str:
        """Name of the span wrapping the whole run."""
        return self._name

    def root_attributes(self, context: ContextT) -> dict[str, Any]:
        """Attributes set on the pipeline span before any stage runs.

        Args:
            context: Pipeline state as handed in by the caller

        Returns:
            dict[str, Any]: Attribute names mapped to values
        """
        return {}

    def result_attributes(self, context: ContextT) -> dict[str, Any]:
        """Attributes set on the pipeline span once every stage has succeeded.

        Args:
            context: Fully populated pipeline state

        Returns:
            dict[str, Any]: Attribute names mapped to values
        """
        return {}

    async def run(self,
                  context: ContextT,
                  parent_carrier: Optional[TraceCarrier] = None) -> ContextT:
        """Run every stage in order under a single parent span.

        Args:
            context: Pipeline state, mutated in place by the stages
            parent_carrier: Trace context from the process that queued this run.
                When present the whole pipeline appears inside that trace rather
                than starting its own.

        Returns:
            ContextT: The same context, now fully populated

        Raises:
            Exception: Whatever a stage raised; remaining stages do not run
        """
        attributes = {OBSERVATION_TYPE: ObservationType.SPAN,
                      **self.root_attributes(context)}

        with traced_span(name = self._name,
                         attributes = attributes,
                         parent_carrier = parent_carrier) as pipeline_span:
            for stage in self._stages:
                # A stage that reports itself a no-op for this run still runs,
                # it just does not get its own observation
                traced = stage.emits_span(context)
                span_context = (traced_span(name = stage.name,
                                            attributes = {OBSERVATION_TYPE: stage.observation_type})
                                if traced else nullcontext(None))

                with span_context as stage_span:
                    await stage.run(context)
                    if stage_span is not None:
                        set_span_attributes(stage_span, stage.span_attributes(context))

                SystemLogger.debug(f"[{self._name.upper()}] Stage '{stage.name}' completed"
                                   f"{'' if traced else ' (untraced)'}")

            set_span_attributes(pipeline_span, self.result_attributes(context))

        return context
