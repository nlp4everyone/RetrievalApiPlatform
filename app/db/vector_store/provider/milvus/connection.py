"""Milvus connection - not implemented yet.

Constructing this is deliberately allowed to fail loudly rather than silently:
if VECTOR_STORE_PROVIDER is set to "milvus", startup should stop with a clear
message instead of booting a service that cannot store anything.
"""
from typing import Any, ClassVar, Optional

from app.db.vector_store.base import BaseVectorStoreConnection
from app.schemas.vector_store.types import VectorStoreType

_NOT_IMPLEMENTED = ("Milvus support is not implemented yet - "
                    "set VECTOR_STORE_PROVIDER=qdrant")


class MilvusConnection(BaseVectorStoreConnection):
    """Placeholder client for Milvus."""

    provider: ClassVar[VectorStoreType] = VectorStoreType.MILVUS

    def __init__(self,
                 uri: str = "http://localhost:19530",
                 token: Optional[str] = None,
                 **kwargs: Any) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    @classmethod
    def from_settings(cls) -> "MilvusConnection":
        """
        Build a Milvus client from MILVUS_URI / MILVUS_TOKEN.

        Raises:
            NotImplementedError: Always, until Milvus support lands
        """
        raise NotImplementedError(_NOT_IMPLEMENTED)

    @property
    def client(self) -> Any:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def check_connection(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)

    async def close(self) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED)