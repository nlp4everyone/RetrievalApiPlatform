"""Long-lived Milvus connection, created once at application startup."""
from typing import Any, ClassVar, Optional

from pymilvus import AsyncMilvusClient

from app.db.vector_store.base import BaseVectorStoreConnection
from app.schemas.vector_store.types import VectorStoreType
from app.core.config import MILVUS_TOKEN, MILVUS_URI


class MilvusConnection(BaseVectorStoreConnection):
    """A connection to a Milvus server, shared by every collection it serves."""

    provider: ClassVar[VectorStoreType] = VectorStoreType.MILVUS

    def __init__(self,
                 uri: str = "http://localhost:19530",
                 token: Optional[str] = None,
                 timeout: float = 10.0,
                 **kwargs: Any) -> None:
        """
        Args:
            uri: Milvus endpoint, e.g. http://172.17.0.1:19530.
            token: Auth token, or None on a server without authentication.
            timeout: Default per-RPC timeout in seconds.
        """
        self.__uri = uri
        self.__token = token
        self.__timeout = timeout

        # Milvus takes "no token" as an empty string rather than None
        self._client = AsyncMilvusClient(uri = uri,
                                         token = token or "",
                                         timeout = timeout,
                                         **kwargs)

    @classmethod
    def from_settings(cls) -> "MilvusConnection":
        """
        Build a Milvus client from MILVUS_URI / MILVUS_TOKEN.

        Returns:
            MilvusConnection: Configured client
        """
        return cls(uri = MILVUS_URI, token = MILVUS_TOKEN)

    async def check_connection(self) -> None:
        """
        Minimal async health check.

        Raises:
            Exception: If Milvus is unreachable
        """
        await self._client.list_collections()

    async def close(self) -> None:
        """Close the underlying client connection."""
        await self._client.close()

    @property
    def client(self) -> AsyncMilvusClient:
        return self._client
