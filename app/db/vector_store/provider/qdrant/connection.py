# Qdrant
from qdrant_client import AsyncQdrantClient
# Typing
from typing import Any, ClassVar, Optional
# Contract
from app.db.vector_store.base import BaseVectorStoreConnection
from app.schemas.vector_store.types import VectorStoreType
# Config
from app.core.config import QDRANT_API_KEY, QDRANT_URL


class QdrantConnection(BaseVectorStoreConnection):
    """Long-lived Qdrant connection, created once at application startup."""

    provider: ClassVar[VectorStoreType] = VectorStoreType.QDRANT

    def __init__(
        self,
        url: str = "http://localhost:6333",
        api_key: Optional[str] = None,
        timeout: float = 10.0,
        **kwargs: Any,
    ) -> None:
        # Config
        self.__url = url
        self.__api_key = api_key
        self.__timeout = timeout

        # Create client
        self._client = AsyncQdrantClient(url=self.__url,
                                         api_key=self.__api_key,
                                         **kwargs)

    @classmethod
    def from_settings(cls) -> "QdrantConnection":
        """
        Build a Qdrant client from QDRANT_URL / QDRANT_API_KEY.

        Returns:
            QdrantConnection: Configured client
        """
        return cls(url = QDRANT_URL, api_key = QDRANT_API_KEY)

    async def check_connection(self) -> None:
        """
        Minimal async health check

        Raises:
            Exception: If Qdrant is unreachable
        """
        await self._client.get_collections()

    async def close(self) -> None:
        """Close the underlying client connection."""
        await self._client.close()

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client