"""The retrieval stage contract - the shared BaseStage bound to RetrievalContext."""
from app.pipelines.base import BaseStage
from app.pipelines.retrieval.context import RetrievalContext


class BaseRetrievalStage(BaseStage[RetrievalContext]):
    """One step of the retrieval pipeline, and one observation in Langfuse.

    See app.pipelines.base.BaseStage for the contract: implement run(), and
    report metrics from span_attributes() rather than opening spans directly.
    """