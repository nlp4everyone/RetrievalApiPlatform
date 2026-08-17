# Inherit
from .base import BaseSparseEmbeddingProvider, SparseVector
from .provider.vllm_sparse_provider import VLLMSparseEmbeddingProvider
# Config
from app.core.config import (SPARSE_EMBEDDING_PROVIDER,
                             SPARSE_EMBEDDING_URL,
                             SPARSE_EMBEDDING_API_KEY,
                             SPARSE_MODEL_NAME)
# Typing
from typing import List

class SparseEmbeddingService:
    """
    Facade over a sparse embedding provider.

    The dense counterpart of this class is EmbeddingService; the two are kept
    apart rather than merged behind one service because a deployment can run
    dense-only (the default), and because their results have different shapes.
    Startup only ever talks to this class - which provider actually serves the
    request is resolved once, from config, in from_settings().
    """

    def __init__(self, provider: BaseSparseEmbeddingProvider) -> None:
        """
        Wrap an already-constructed sparse embedding provider.

        Args:
            provider (BaseSparseEmbeddingProvider): The provider that actually
                performs sparse embedding calls
        """
        self._provider = provider

    async def embed(self, texts: List[str]) -> List[SparseVector]:
        """
        Generate sparse embeddings for a list of texts.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[SparseVector]: {token_id: weight} mapping for each input text,
                same order as input
        """
        return await self._provider.embed(texts)

    async def check_connection(self) -> None:
        """
        Probe the underlying provider with a throwaway request so a
        misconfigured or unreachable sparse server fails at startup rather
        than on the first ingestion.

        Raises:
            Exception: If the provider is unreachable or returns an error
            RuntimeError: If the provider answers but produces an empty embedding,
                which means the served model is not a token-classification one
        """
        result = await self._provider.embed(["Hello"])
        if not result or not result[0]:
            raise RuntimeError(f"Sparse embedding provider returned an empty embedding "
                               f"(model={SPARSE_MODEL_NAME!r}) - is the served model a "
                               f"token-classification one?")

    async def aclose(self) -> None:
        """Close the underlying provider's connections (called on shutdown)."""
        await self._provider.aclose()

    @classmethod
    def from_settings(cls) -> "SparseEmbeddingService":
        """
        Build the SparseEmbeddingService for the provider selected via
        SPARSE_EMBEDDING_PROVIDER.

        Returns:
            SparseEmbeddingService: Instance wrapping the configured provider
                ("vllm" -> VLLMSparseEmbeddingProvider)

        Raises:
            ValueError: If SPARSE_EMBEDDING_PROVIDER is set to an unsupported value
        """
        if SPARSE_EMBEDDING_PROVIDER == "vllm":
            provider = VLLMSparseEmbeddingProvider(base_url = SPARSE_EMBEDDING_URL,
                                                   api_key = SPARSE_EMBEDDING_API_KEY,
                                                   model = SPARSE_MODEL_NAME)
        else:
            raise ValueError(f"Unsupported SPARSE_EMBEDDING_PROVIDER: {SPARSE_EMBEDDING_PROVIDER!r}")
        return cls(provider)