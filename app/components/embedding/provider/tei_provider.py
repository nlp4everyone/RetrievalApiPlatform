# Inherit
from ..base import BaseEmbeddingProvider
# Other component
import httpx
# Typing
from typing import List, Optional

class TEIEmbeddingProvider(BaseEmbeddingProvider):
    """
    Fetch dense embeddings from a Text Embeddings Inference (TEI) /embed endpoint.

    Unlike the OpenAI embeddings API, TEI's /embed endpoint takes a plain
    {"inputs": [...]} body and returns the list of vectors directly (no
    wrapping "data"/"embedding" envelope).
    """

    def __init__(self,
                base_url: str,
                api_key: Optional[str] = None,
                timeout: float = 30.0,
                max_connections: int = 20) -> None:
        """
        Configure the TEI HTTP client.

        The client is built once and reused for every embed() call instead of
        per-batch, since constructing httpx.AsyncClient costs a synchronous
        ~14ms event-loop block. max_connections caps concurrent sockets to
        TEI, since this pool is shared process-wide, not just per file.

        Args:
            base_url (str): Base URL of the TEI service (the "/embed" path is appended)
            api_key (Optional[str]): Bearer token to send; omitted entirely if the TEI
                server does not require authentication
            timeout (float): Request timeout in seconds (default: 30.0)
            max_connections (int): Ceiling on concurrent connections to the TEI
                server from this process (default: 20)
        """
        self._url = f"{base_url.rstrip('/')}/embed"
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(timeout = timeout,
                                         limits = httpx.Limits(max_connections = max_connections))

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        Generate dense embedding vectors via a raw POST to TEI's /embed endpoint.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[List[float]]: Embedding vector for each input text, same order as input

        Raises:
            httpx.HTTPStatusError: If the TEI service returns a non-2xx response
        """
        response = await self._client.post(self._url,
                                           headers = self._headers,
                                           json = {"inputs": texts})
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        """Close the underlying HTTP client and its connection pool."""
        await self._client.aclose()