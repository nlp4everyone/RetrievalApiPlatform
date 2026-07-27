"""File ingestion: download -> parse -> chunk -> embed -> index.

Each step is a BaseIngestionStage; IngestionPipeline runs them in order and
wraps each one in its own Langfuse observation.
"""
from .base import BaseIngestionStage
from .context import IngestionContext
from .pipeline import IngestionPipeline
from .factory import build_ingestion_pipeline