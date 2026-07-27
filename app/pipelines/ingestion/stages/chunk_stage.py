"""Stage 3 - split the parsed text into chunks."""
from typing import Any, ClassVar

from app.components.chunking import ChunkingService
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext


class ChunkStage(BaseIngestionStage):
    """Split text into the chunks that will be embedded and indexed."""

    name: ClassVar[str] = "chunk"

    def __init__(self, chunking_service: ChunkingService) -> None:
        """
        Args:
            chunking_service: Service wrapping the configured chunking provider
        """
        self._chunking_service = chunking_service

    async def run(self, context: IngestionContext) -> None:
        """Populate context.chunks."""
        context.chunks = await self._chunking_service.split_text(context.text or "")

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        num_chunks = len(context.chunks)
        total_chars = sum(len(chunk) for chunk in context.chunks)
        return {
            "chunk.strategy": context.chunking_strategy,
            "chunk.provider": self._chunking_service.strategy_name,
            "chunk.size": context.chunk_size,
            "chunk.overlap": context.chunk_overlap,
            "chunks.count": num_chunks,
            "chunks.avg_chars": round(total_chars / num_chunks) if num_chunks else 0,
        }