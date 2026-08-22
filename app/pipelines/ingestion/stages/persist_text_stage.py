"""Stage 3 - keep the parsed Markdown, so a run can be traced back and reused.

Parsing is the only step whose output cannot be reproduced for free: it costs
money per page, it is rate limited, and the vendor's model can change under
you. Storing what came back turns "why did this store answer that?" into
reading a file, and lets the same document be re-ingested with different
chunking without paying the parser again (ParseStage reads what this writes).

Writing the artifact is never worth failing an ingestion over: the vector
store is already correct without it. Every failure here is logged and
swallowed, which is why this stage has no exceptions of its own to document.
"""
from typing import Any, ClassVar

from minio import Minio

from app.db.minio import MinioFileStore
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.pipelines.ingestion.parsed_cache import parsed_text_key
from app.startup import get_io_executor, get_storage_semaphore
from loggers import SystemLogger


class PersistTextStage(BaseIngestionStage):
    """Write the parsed Markdown to object storage, best effort."""

    name: ClassVar[str] = "persist_text"

    def __init__(self, minio_client: Minio, bucket: str) -> None:
        """
        Args:
            minio_client: Connected MinIO client
            bucket: Bucket the parsed Markdown is written to
        """
        self._minio_client = minio_client
        self._bucket = bucket

    def emits_span(self, context: IngestionContext) -> bool:
        """Skip the span when the text came from the cache - there is nothing
        to write, and a no-op step is noise in the trace."""
        return not context.parsed_from_cache

    async def run(self, context: IngestionContext) -> None:
        """
        Store context.text under its content-addressed key.

        Does nothing when parsing was served from the cache (the artifact is
        already there), when there is no text, or when the file's hash is
        missing - without it there is no key to store the text under.
        """
        context.metrics["parsed_text_saved"] = False

        if context.parsed_from_cache or not context.text or not context.content_sha256:
            return

        provider_name = context.metrics.get("parser_provider")
        if not provider_name:
            return

        key = parsed_text_key(api_key = context.api_key,
                              provider = provider_name,
                              content_sha256 = context.content_sha256)
        payload = context.text.encode("utf-8")

        try:
            async with get_storage_semaphore():
                await MinioFileStore.upload_file(minio_client = self._minio_client,
                                                 file_buffer = payload,
                                                 file_name = key,
                                                 bucket_name = self._bucket,
                                                 content_type = "text/markdown",
                                                 executor = get_io_executor())
        except Exception as e:
            # Deliberately not re-raised: see the module docstring
            SystemLogger.warning(f"[WORKER] Could not store parsed text at {key}: {e!r}")
            return

        context.metrics["parsed_text_saved"] = True
        context.metrics["parsed_text_key"] = key
        context.metrics["parsed_text_bytes"] = len(payload)

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "storage.type": "minio",
            "storage.bucket": self._bucket,
            # Same pair DownloadStage reports for the file it read, so both
            # ends of an ingestion name an object the same way
            "storage.path": context.metrics.get("parsed_text_key"),
            "parsed_text.saved": context.metrics.get("parsed_text_saved"),
            "parsed_text.size_bytes": context.metrics.get("parsed_text_bytes"),
        }