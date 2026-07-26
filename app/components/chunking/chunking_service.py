# Inherit
from .base import BaseChunkingProvider
from .provider.chonkie_provider import ChonkieProvider
from .provider.langchain_provider import LangchainProvider
# Config
from app.core.config import CHUNKING_PROVIDER
# Schemas
from app.schemas.chunking import ChonkieChunkingConfig, LangchainChunkingConfig
# Typing
from typing import List, Optional


class ChunkingService:
    """
    Facade over a text chunking provider (Chonkie or LangChain).

    Callers only ever talk to this class - which library actually splits the
    text is resolved from config in from_settings().

    Unlike EmbeddingService and ParsingService, this one is built per vector
    store rather than once at startup: chunk size and overlap come from the
    create request, not from application config.
    """

    def __init__(self, provider: BaseChunkingProvider) -> None:
        """
        Wrap an already-constructed chunking provider.

        Args:
            provider (BaseChunkingProvider): The provider that performs the split
        """
        self._provider = provider

    @property
    def strategy_name(self) -> str:
        """
        Name of the active splitting strategy, reported on traces.

        Returns:
            str: Strategy identifier, e.g. "chonkie:recursive"
        """
        return self._provider.strategy_name

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
    def from_settings(cls,
                      chunking_strategy: str = "auto",
                      chunk_size: Optional[int] = None,
                      chunk_overlap: Optional[int] = None) -> "ChunkingService":
        """
        Build the ChunkingService for the provider selected via CHUNKING_PROVIDER.

        The vector store's chunking_strategy ("auto", "static", "fuse") only
        carries sizing intent, so every value currently resolves to the
        provider's default splitter with the requested size and overlap. New
        splitters - MarkdownHeader, Slumber, Unstructured - plug in here.

        Args:
            chunking_strategy (str): Strategy recorded on the vector store
            chunk_size (Optional[int]): Maximum chunk size; provider default when None
            chunk_overlap (Optional[int]): Overlap between chunks; provider default when None

        Returns:
            ChunkingService: Instance wrapping the configured provider
                ("chonkie" -> ChonkieProvider, "langchain" -> LangchainProvider)

        Raises:
            ValueError: If CHUNKING_PROVIDER is set to an unsupported value
        """
        config_kwargs: dict[str, int] = {}
        if chunk_size is not None:
            config_kwargs["chunk_size"] = chunk_size
        if chunk_overlap is not None:
            config_kwargs["chunk_overlap"] = chunk_overlap

        if CHUNKING_PROVIDER == "chonkie":
            provider = ChonkieProvider(config = ChonkieChunkingConfig(**config_kwargs))
        elif CHUNKING_PROVIDER == "langchain":
            provider = LangchainProvider(config = LangchainChunkingConfig(**config_kwargs))
        else:
            raise ValueError(f"Unsupported CHUNKING_PROVIDER: {CHUNKING_PROVIDER!r}")
        return cls(provider)