from abc import ABC, abstractmethod
from typing import Dict, List

# A sparse embedding is a {token_id: weight} mapping rather than a fixed-length
# vector - only the tokens the model actually assigned weight to are carried,
# which is what makes it cheap to store and to match lexically.
SparseVector = Dict[int, float]

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

    @abstractmethod
    async def aclose(self) -> None:
        """Release any connections the provider holds (called on app/worker shutdown)."""
        pass


class BaseSparseEmbeddingProvider(ABC):
    """
    Interface for a sparse (lexical) embedding backend.

    Kept separate from BaseEmbeddingProvider because the two return different
    shapes - a dense vector vs a {token_id: weight} mapping - and a store can
    legitimately be fed by one, the other, or both. SparseEmbeddingService
    depends only on this interface, so a new backend can be added without
    touching startup or callers.
    """

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[SparseVector]:
        """
        Generate a sparse embedding for each input text.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[SparseVector]: {token_id: weight} mapping for each input text,
                same order as input

        Raises:
            Exception: If the underlying embedding backend is unreachable or returns an error
        """
        pass

    @abstractmethod
    async def aclose(self) -> None:
        """Release any connections the provider holds (called on app/worker shutdown)."""
        pass