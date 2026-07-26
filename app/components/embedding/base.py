from abc import ABC, abstractmethod
from typing import List

class BaseEmbeddingProvider(ABC):
    """
    Interface for a dense embedding backend.

    Concrete providers wrap a specific way of reaching an embedding
    model (an OpenAI-compatible SDK call, a raw HTTP request to a TEI
    server, etc). EmbeddingService depends only on this interface, so
    a new backend can be added without touching startup or callers.
    """

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate a dense embedding vector for each input text.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[List[float]]: Embedding vector for each input text, same order as input

        Raises:
            Exception: If the underlying embedding backend is unreachable or returns an error
        """
        pass