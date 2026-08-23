"""Stage 3 - split the parsed text into chunks."""
from typing import Any, ClassVar, Optional

from app.components.chunking import ChunkingService, detect_strategy
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.schemas.chunking import ChunkingStrategy


class ChunkStage(BaseIngestionStage):
    """Split text into the chunks that will be embedded and indexed.

    The splitter is settled here rather than when the pipeline is built: a
    request that names none has to be answered from the document, and the
    document does not exist yet at create time - only its file id does.
    """

    name: ClassVar[str] = "chunk"

    def __init__(self, splitter: Optional[ChunkingStrategy] = None) -> None:
        """
        Args:
            splitter: Splitter the request asked for; None means detect it
                from the parsed text
        """
        self._splitter = splitter

    async def run(self, context: IngestionContext) -> None:
        """Resolve the splitter, then populate context.chunks."""
        detected = self._splitter is None
        splitter = self._splitter or detect_strategy(context.text or "",
                                                     context.file_extension)
        # A splitter with no overlap is asked for 0 rather than the requested
        # value, so a request that named one does not look like it was honoured
        overlap = context.chunk_overlap if splitter.supports_overlap else 0

        service = ChunkingService.for_strategy(strategy = splitter,
                                               chunk_size = context.chunk_size,
                                               chunk_overlap = overlap)

        # Written back so the span, and the record the worker persists, both
        # describe what ran rather than what was asked for. Read off the
        # service, not off the request: under "auto" sizing both arrive here
        # as None and the strategy's own defaults decide - and None would be
        # dropped from the span entirely (set_span_attributes skips it),
        # losing the numbers instead of reporting them.
        context.splitter = splitter.value
        context.splitter_detected = detected
        context.chunk_size = service.chunk_size
        context.chunk_overlap = service.chunk_overlap

        context.chunks = await service.split_text(context.text or "")

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        num_chunks = len(context.chunks)
        total_chars = sum(len(chunk) for chunk in context.chunks)
        return {
            # The splitter that ran - markdown, recursive, ... The vector
            # store's auto/static sizing intent is on the parent
            # enqueue_ingestion span, where it belongs
            "chunk.strategy": context.splitter,
            "chunk.detected": context.splitter_detected,
            "chunk.size": context.chunk_size,
            "chunk.overlap": context.chunk_overlap,
            "chunks.count": num_chunks,
            "chunks.avg_chars": round(total_chars / num_chunks) if num_chunks else 0,
        }
