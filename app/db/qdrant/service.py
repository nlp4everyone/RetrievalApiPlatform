# Qdrant
from qdrant_client import AsyncQdrantClient
# Typing
from typing import Optional

class QdrantService:
    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        **kwargs,
    ):
        # Config
        self.__url = url
        self.__api_key = api_key
        self.__timeout = timeout

        # Create client
        self._client = AsyncQdrantClient(url=self.__url,
                                         api_key=self.__api_key,
                                         **kwargs)


    async def _check_connection(self) -> None:
        """
        Minimal async health check
        """
        await self._client.get_collections()

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client
