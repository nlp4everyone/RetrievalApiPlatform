"""State carried through an ingestion run.

One mutable object flows through every stage: each stage reads what earlier
stages produced and writes its own result. That is what lets the stages stay
independent of each other and of the pipeline that runs them.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class IngestionContext:
    """Inputs, intermediate results, and metrics for ingesting one file."""

    # --- Inputs, set by the caller ---------------------------------------
    vector_store_id: str
    api_key: str
    file_id: str
    file_metadata: dict[str, Any]
    chunking_strategy: str = "auto"
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None

    # --- Filled in progressively by the stages ---------------------------
    raw_bytes: Optional[bytes] = None          # download
    text: Optional[str] = None                 # parse
    chunks: list[str] = field(default_factory=list)  # chunk
    num_inserted: int = 0                      # embed + index

    # Free-form numbers a stage wants on its span but that are not pipeline
    # state (parser class name, whether the collection was created, ...)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def filename(self) -> Optional[str]:
        """Original filename recorded when the file was uploaded."""
        return self.file_metadata.get("filename")

    @property
    def file_extension(self) -> str:
        """Lower-cased extension including the dot, e.g. '.pdf'."""
        return Path(self.filename or "").suffix.lower()

    @property
    def storage_bucket(self) -> Optional[str]:
        """MinIO bucket holding the file."""
        return self.file_metadata.get("minio_bucket")

    @property
    def storage_path(self) -> Optional[str]:
        """Object path within the bucket."""
        return self.file_metadata.get("minio_path")