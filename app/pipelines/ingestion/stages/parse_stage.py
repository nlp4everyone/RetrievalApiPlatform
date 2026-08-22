"""Stage 2 - turn the raw bytes into plain text."""
from typing import Any, ClassVar, Optional

from minio import Minio

from app.db.minio import MinioFileStore
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.pipelines.ingestion.parsed_cache import parsed_text_key
from app.components.parsing import ParsingService
from app.startup import get_io_executor, get_storage_semaphore
from loggers import SystemLogger


class ParseStage(BaseIngestionStage):
    """Extract text using the parsing provider registered for this format.

    Parsing is the one step that leaves this deployment - it is billed per
    page, rate limited by the vendor, and by far the slowest stage. So before
    calling out, this checks whether the same bytes have already been parsed
    by the same backend (see app.pipelines.ingestion.parsed_cache) and reuses
    that Markdown when they have. Re-ingesting a file with different chunking
    then costs nothing on this side.
    """

    name: ClassVar[str] = "parse"

    def __init__(self,
                 parsing_service: ParsingService,
                 minio_client: Optional[Minio] = None,
                 cache_bucket: Optional[str] = None) -> None:
        """
        Args:
            parsing_service: Service resolving a provider per file extension
            minio_client: Connected MinIO client holding the parse cache.
                None disables the cache lookup entirely
            cache_bucket: Bucket the cached Markdown lives in
        """
        self._parsing_service = parsing_service
        self._minio_client = minio_client
        self._cache_bucket = cache_bucket

    async def run(self, context: IngestionContext) -> None:
        """
        Populate context.text, from the cache when possible and from the
        parsing provider otherwise.

        Raises:
            ValueError: If the extension has no registered provider
        """
        provider = self._parsing_service.provider_for(context.file_extension)

        # Recorded for the span - which provider ran is the first thing worth
        # knowing when a document produces unexpected text
        context.metrics["parser_provider"] = provider.name

        cached_text = await self._load_from_cache(context, provider.name)
        if cached_text is not None:
            context.text = cached_text
            context.parsed_from_cache = True
            context.metrics["parse_cache"] = "hit"
            SystemLogger.info(f"[WORKER] Parse cache hit for file {context.file_id} "
                              f"({provider.name}); skipping the parsing API")
            return

        context.metrics["parse_cache"] = "miss"
        context.text = await provider.parse(context.raw_bytes, context.file_extension)

    async def _load_from_cache(self,
                               context: IngestionContext,
                               provider_name: str) -> Optional[str]:
        """
        Read this file's parsed Markdown, if it was stored on an earlier run.

        A cache that cannot be reached is not an error: parsing again is always
        correct, only slower and more expensive, so every failure here degrades
        to a miss rather than failing an ingestion that would otherwise work.

        Args:
            context: Pipeline state, after download filled in content_sha256
            provider_name: Backend that would parse this file

        Returns:
            Optional[str]: The cached Markdown, or None when it is not there
        """
        if self._minio_client is None or not context.content_sha256:
            return None

        key = parsed_text_key(api_key = context.api_key,
                              provider = provider_name,
                              content_sha256 = context.content_sha256)
        try:
            async with get_storage_semaphore():
                if not await MinioFileStore.object_exists(minio_client = self._minio_client,
                                                          bucket_name = self._cache_bucket,
                                                          file_path = key,
                                                          executor = get_io_executor()):
                    return None
                cached = await MinioFileStore.download_file(minio_client = self._minio_client,
                                                            bucket_name = self._cache_bucket,
                                                            file_path = key,
                                                            executor = get_io_executor())
        except Exception as e:
            SystemLogger.warning(f"[WORKER] Parse cache unavailable for {key}: {e!r} - parsing instead")
            return None

        return cached.decode("utf-8") if cached else None

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "parser.provider": context.metrics.get("parser_provider"),
            "parser.cache": context.metrics.get("parse_cache"),
            "file.extension": context.file_extension,
            "text.num_chars": len(context.text or ""),
        }