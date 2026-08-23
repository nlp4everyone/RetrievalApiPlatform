# Inherit
from .base import BaseChunkingProvider
from .registry import REGISTRY
# Schemas
from app.schemas.chunking import ChunkingStrategy
from pydantic import BaseModel
# Typing
from typing import List, Optional
# Logging
from loggers import SystemLogger


class ChunkingService:
    """
    Facade over a text chunking provider.

    Callers only ever talk to this class - which library actually splits the
    text follows from the strategy, via app.components.chunking.registry.

    Unlike EmbeddingService and ParsingService, this one is built per file
    rather than once at startup: chunk size and overlap come from the create
    request, and the strategy may be detected from the document itself.
    """

    def __init__(self, provider: BaseChunkingProvider, config: BaseModel) -> None:
        """
        Wrap an already-constructed chunking provider.

        Args:
            provider (BaseChunkingProvider): The provider that performs the split
            config (BaseModel): The config it was built from, kept so callers
                can report the sizing that actually applied
        """
        self._provider = provider
        self._config = config

    @property
    def chunk_size(self) -> int:
        """Chunk size in effect, whether requested or defaulted by the strategy."""
        return self._config.chunk_size

    @property
    def chunk_overlap(self) -> int:
        """Overlap in effect; 0 for strategies that have no overlap knob."""
        return getattr(self._config, "chunk_overlap", 0)

    async def split_text(self, text: str) -> List[str]:
        """
        Split text into chunks.

        Args:
            text (str): Text to split

        Returns:
            List[str]: Chunks in document order
        """
        return await self._provider.split_text(text)

    @classmethod
    def for_strategy(cls,
                     strategy: ChunkingStrategy,
                     chunk_size: Optional[int] = None,
                     chunk_overlap: Optional[int] = None) -> "ChunkingService":
        """
        Build the service for one strategy, at the requested size.

        Args:
            strategy (ChunkingStrategy): Splitter to run
            chunk_size (Optional[int]): Maximum chunk size; the strategy's own
                default when None - which is what "auto" sizing means, and why
                markdown can be sized differently from the window splitters
            chunk_overlap (Optional[int]): Overlap between chunks; ignored for
                strategies that have no such knob, the strategy's default when None

        Returns:
            ChunkingService: Instance wrapping the provider for that strategy
        """
        spec = REGISTRY[strategy]

        config_kwargs: dict[str, object] = {}
        if chunk_size is not None:
            config_kwargs["chunk_size"] = chunk_size
        if chunk_overlap is not None and strategy.supports_overlap:
            config_kwargs["chunk_overlap"] = cls._bounded_overlap(chunk_overlap, chunk_size)

        config = spec.config_model(**config_kwargs)
        return cls(spec.build(config), config)

    @staticmethod
    def _bounded_overlap(chunk_overlap: int, chunk_size: Optional[int]) -> int:
        """
        Keep overlap under chunk size, so the splitter always advances.

        A request may name any overlap it likes - the API accepts overlap and
        size independently, and until strategies other than recursive became
        reachable the value was never used. LangChain's splitters raise on
        overlap >= size, which in the worker means a store stuck IN_PROGRESS,
        so degrade rather than fail on a request that was accepted at the door.

        Args:
            chunk_overlap (int): Overlap the caller asked for
            chunk_size (Optional[int]): Size the caller asked for, if any

        Returns:
            int: The requested overlap, or at most half the chunk size
        """
        if chunk_size is None or chunk_overlap < chunk_size:
            return chunk_overlap
        bounded = chunk_size // 2
        SystemLogger.warning(f"chunk_overlap {chunk_overlap} is not smaller than chunk_size "
                             f"{chunk_size}; using {bounded} instead")
        return bounded
