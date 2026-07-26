from abc import ABC, abstractmethod
from typing import List

class BaseChunkingProvider(ABC):
    """
    Interface for a text chunking backend.

    Concrete providers wrap a specific splitting library (Chonkie, LangChain
    text splitters, ...). ChunkingService depends only on this interface, so a
    new backend can be added without touching the ingestion pipeline.
    """

    @abstractmethod
    async def split_text(self, text: str) -> List[str]:
        """
        Split text into chunks.

        Args:
            text (str): Text to split

        Returns:
            List[str]: Chunks in document order

        Raises:
            Exception: If the underlying splitter rejects the configuration
        """
        pass

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """
        Human-readable name of the splitting strategy, reported on traces.

        Returns:
            str: Strategy identifier, e.g. "chonkie:recursive"
        """