"""Stage 2 - turn the raw bytes into plain text."""
from typing import Any, ClassVar

from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.components.parsing import ParsingService


class ParseStage(BaseIngestionStage):
    """Extract text using the parsing provider registered for this format."""

    name: ClassVar[str] = "parse"

    def __init__(self, parsing_service: ParsingService) -> None:
        """
        Args:
            parsing_service: Service resolving a provider per file extension
        """
        self._parsing_service = parsing_service

    async def run(self, context: IngestionContext) -> None:
        """
        Populate context.text using the provider for this file's extension.

        Raises:
            ValueError: If the extension has no registered provider
        """
        provider = self._parsing_service.provider_for(context.file_extension)

        # Recorded for the span - which provider ran is the first thing worth
        # knowing when a document produces unexpected text
        context.metrics["parser_provider"] = type(provider).__name__

        context.text = await provider.parse(context.raw_bytes, context.file_extension)

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "parser.provider": context.metrics.get("parser_provider"),
            "file.extension": context.file_extension,
            "text.num_chars": len(context.text or ""),
        }