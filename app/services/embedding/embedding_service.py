# Inherit
from .base import BaseEmbeddingProvider
from .openai_provider import OpenAIEmbeddingProvider
from .tei_provider import TEIEmbeddingProvider
# Config
from app.core.config import (EMBEDDING_PROVIDER,
                             VLLM_DENSE_EMBEDDING_URL,
                             DENSE_MODEL_NAME,
                             TEI_EMBEDDING_URL,
                             TEI_API_KEY,
                             SERVING_API_KEY)
# Typing
from typing import List

class EmbeddingService:
    """
    Facade over a dense embedding provider (OpenAI-compatible or TEI).

    Startup only ever talks to this class - which provider actually serves
    the request is resolved once, from config, in from_settings().
    """

    def __init__(self, provider: BaseEmbeddingProvider) -> None:
        """
        Wrap an already-constructed embedding provider.

        Args:
            provider (BaseEmbeddingProvider): The provider that actually performs embedding calls
        """
        self._provider = provider

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate dense embedding vectors for a list of texts.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[List[float]]: Embedding vector for each input text, same order as input
        """
        return await self._provider.embed(texts)

    async def check_connection(self) -> None:
        """
        Probe the underlying provider with a throwaway request.

        Raises:
            Exception: If the provider is unreachable or returns an error
        """
        await self._provider.embed(["Hello"])

    @classmethod
    def from_settings(cls) -> "EmbeddingService":
        """
        Build the EmbeddingService for the provider selected via EMBEDDING_PROVIDER.

        Returns:
            EmbeddingService: Instance wrapping the configured provider
                ("openai" -> OpenAIEmbeddingProvider, "tei" -> TEIEmbeddingProvider)

        Raises:
            ValueError: If EMBEDDING_PROVIDER is set to an unsupported value
        """
        if EMBEDDING_PROVIDER == "tei":
            provider = TEIEmbeddingProvider(base_url = TEI_EMBEDDING_URL,
                                            api_key = TEI_API_KEY)
        elif EMBEDDING_PROVIDER == "openai":
            provider = OpenAIEmbeddingProvider(base_url = VLLM_DENSE_EMBEDDING_URL,
                                               api_key = SERVING_API_KEY,
                                               model = DENSE_MODEL_NAME)
        else:
            raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER!r}")
        return cls(provider)