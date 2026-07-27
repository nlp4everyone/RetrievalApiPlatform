"""The ingestion stage contract - the shared BaseStage bound to IngestionContext."""
from app.pipelines.base import BaseStage
from app.pipelines.ingestion.context import IngestionContext


class BaseIngestionStage(BaseStage[IngestionContext]):
    """One step of the ingestion pipeline, and one observation in Langfuse.

    See app.pipelines.base.BaseStage for the contract: implement run(), and
    report metrics from span_attributes() rather than opening spans directly.
    """
