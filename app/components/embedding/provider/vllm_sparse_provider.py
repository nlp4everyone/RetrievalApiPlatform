# Inherit
from ..base import BaseSparseEmbeddingProvider, SparseVector
# Other component
import asyncio
import httpx
# Typing
from typing import List, Optional

class VLLMSparseEmbeddingProvider(BaseSparseEmbeddingProvider):
    """
    Fetch sparse (lexical) embeddings from a vLLM server running a BGE-M3
    style token-classification model.

    vLLM has no single "sparse embedding" endpoint, so this takes two calls:

    1. ``/tokenize`` - the token ids of each text, which the pooling response
       does not echo back but which are the keys of the sparse vector.
    2. ``/pooling`` - one weight per token, in token order.

    Zipping the two gives the {token_id: weight} mapping. Note both paths live
    at the server root, not under ``/v1`` - a trailing ``/v1`` on the
    configured base URL is stripped so the same URL style as
    DENSE_EMBEDDING_URL can be pasted in.
    """

    def __init__(self,
                base_url: str,
                model: str,
                api_key: Optional[str] = None,
                timeout: float = 30.0) -> None:
        """
        Configure the vLLM HTTP client.

        Args:
            base_url (str): Base URL of the vLLM server (a trailing "/v1" is
                stripped; "/tokenize" and "/pooling" are appended)
            model (str): Model name sent with every request
            api_key (Optional[str]): Bearer token to send; omitted entirely if
                the server does not require authentication
            timeout (float): Request timeout in seconds (default: 30.0)
        """
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-len("/v1")]
        self._tokenize_url = f"{root}/tokenize"
        self._pooling_url = f"{root}/pooling"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout

    async def _tokenize(self,
                        client: httpx.AsyncClient,
                        texts: List[str]) -> List[List[int]]:
        """
        Get the token ids of each text.

        vLLM's /tokenize takes a single prompt per call, so the texts are sent
        concurrently on the caller's client rather than one after another.

        Args:
            client (httpx.AsyncClient): Client to issue the requests on
            texts (List[str]): Texts to tokenize

        Returns:
            List[List[int]]: Token ids for each input text, same order as input

        Raises:
            httpx.HTTPStatusError: If the vLLM server returns a non-2xx response
        """
        responses: List[httpx.Response] = await asyncio.gather(
            *[client.post(self._tokenize_url,
                          headers = self._headers,
                          json = {"model": self._model, "prompt": text})
              for text in texts]
        )
        for response in responses:
            response.raise_for_status()
        return [response.json()["tokens"] for response in responses]

    async def embed(self, texts: List[str]) -> List[SparseVector]:
        """
        Generate sparse embeddings via vLLM's /tokenize + /pooling endpoints.

        Duplicate tokens are collapsed to their highest weight (the usual
        BGE-M3 convention), and non-positive weights are dropped so the result
        stays sparse instead of carrying an entry per vocabulary position.

        Args:
            texts (List[str]): Texts to embed

        Returns:
            List[SparseVector]: {token_id: weight} mapping for each input text,
                same order as input

        Raises:
            httpx.HTTPStatusError: If the vLLM server returns a non-2xx response
            KeyError: If the server's response is missing the expected fields
        """
        if not texts:
            return []

        async with httpx.AsyncClient(timeout = self._timeout) as client:
            # Token ids and token weights come from two different endpoints,
            # so both are fetched before either can be interpreted
            all_tokens = await self._tokenize(client, texts)
            response = await client.post(self._pooling_url,
                                         headers = self._headers,
                                         json = {"model": self._model, "input": texts})
        response.raise_for_status()
        all_weights = [item["data"] for item in response.json()["data"]]

        sparse_vectors: List[SparseVector] = []
        for tokens, weights in zip(all_tokens, all_weights):
            # The BOS token carries no lexical meaning; /pooling does not emit
            # a weight for it, so leaving it in would shift every pairing by one
            if tokens and tokens[0] == 0:
                tokens = tokens[1:]

            sparse_vector: SparseVector = {}
            for token, weight in zip(tokens, weights):
                if weight <= 0:
                    continue
                sparse_vector[token] = max(weight, sparse_vector.get(token, 0.0))
            sparse_vectors.append(sparse_vector)
        return sparse_vectors