# Inherit
from ..base import BaseEmbeddingProvider
# OpenAI client
from openai import AsyncOpenAI
# Typing
from typing import List

class OpenAIEmbeddingProvider(BaseEmbeddingProvider):
    """Fetch dense embeddings from an OpenAI-compatible endpoint (e.g. vLLM)"""

    def __init__(self,
                base_url: str,
                api_key: str,
                model: str) -> None:
        """
        Configure the OpenAI-compatible embedding client.

        Args:
            base_url (str): Base URL of the OpenAI-compatible embedding endpoint
            api_key (str): API key sent as the client's bearer token
            model (str): Model name passed to embeddings.create
        """
        self._model = model
        self._client = AsyncOpenAI(base_url = base_url, api_key = api_key)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate dense embedding vectors via the OpenAI embeddings API.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[List[float]]: Embedding vector for each input text, same order as input
        """
        response = await self._client.embeddings.create(model = self._model, input = texts)
        return [item.embedding for item in response.data]

    async def aclose(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        await self._client.close()