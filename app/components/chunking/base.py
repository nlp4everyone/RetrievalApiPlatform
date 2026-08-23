from abc import ABC, abstractmethod
from typing import List

class BaseChunkingProvider(ABC):
    """
    Interface for a text chunking backend.

    Concrete providers wrap a specific splitting library (Chonkie, LangChain
    text splitters, ...). ChunkingService depends only on this interface, so a
    new backend can be added without touching the ingestion pipeline.

    Providers no longer report which strategy they are running: the strategy
    selects the implementation (see .registry), so the caller already knows.
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
