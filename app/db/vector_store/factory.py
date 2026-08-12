"""Resolve a vector store implementation from a provider name.

Startup registers the live connection for each configured provider; call sites
then ask for a collection by name and get back a ``BaseAsyncVectorStore``
without knowing which backend serves it.

Registration is what keeps the import graph acyclic: this module never imports
``app.startup``, startup pushes its connections down here instead.
"""
from importlib import import_module
from typing import NamedTuple, Optional, Type

from app.db.vector_store.base import BaseAsyncVectorStore, BaseVectorStoreConnection
from app.schemas.vector_store.types import VectorStoreType
from app.core.config import VECTOR_STORE_PROVIDER, VECTOR_STORE_PROVIDERS


class _Backend(NamedTuple):
    """Where a provider's implementation lives, resolved on first use."""
    module: str
    store_class: str
    connection_class: str


# Backends are addressed by module path rather than imported at module level:
# app.db.vector_store.provider.qdrant imports app.db.vector_store.base, so
# importing them here would close an import cycle. Resolution happens on first use instead, which also
# means a backend whose SDK is not installed only fails when it is asked for.
_BACKENDS: dict[VectorStoreType, _Backend] = {
    VectorStoreType.QDRANT: _Backend("app.db.vector_store.provider.qdrant", "AsyncQdrantVectorStore", "QdrantConnection"),
    VectorStoreType.MILVUS: _Backend("app.db.vector_store.provider.milvus", "AsyncMilvusVectorStore", "MilvusConnection"),
}


class VectorStoreFactory:
    """Builds per-collection vector stores from connections registered at startup."""

    # Live connections, populated by app.startup
    _connections: dict[VectorStoreType, BaseVectorStoreConnection] = {}

    @staticmethod
    def _backend(provider: VectorStoreType) -> _Backend:
        """Look up where a provider's implementation lives.

        Args:
            provider: Backend to resolve

        Returns:
            _Backend: Module path and class names

        Raises:
            ValueError: If the provider has no registered backend
        """
        backend = _BACKENDS.get(provider)
        if backend is None:
            raise ValueError(f"No vector store backend registered for provider: {provider}")
        return backend

    @staticmethod
    def default_provider() -> VectorStoreType:
        """The provider new vector stores are created with.

        Returns:
            VectorStoreType: Provider selected by VECTOR_STORE_PROVIDER
        """
        return VectorStoreType(VECTOR_STORE_PROVIDER)

    @staticmethod
    def enabled_providers() -> tuple[VectorStoreType, ...]:
        """Every provider startup should connect, default first.

        Returns:
            tuple[VectorStoreType, ...]: Providers selected by VECTOR_STORE_PROVIDERS
        """
        return tuple(VectorStoreType(provider) for provider in VECTOR_STORE_PROVIDERS)

    @classmethod
    def connection_class(cls, provider: VectorStoreType) -> Type[BaseVectorStoreConnection]:
        """Look up the connection class for a provider.

        Args:
            provider: Provider to connect to

        Returns:
            Type[BaseVectorStoreConnection]: The connection class

        Raises:
            ValueError: If the provider has no registered connection class
        """
        backend = cls._backend(provider)
        return getattr(import_module(backend.module), backend.connection_class)

    @classmethod
    def register_connection(cls,
                        provider: VectorStoreType,
                        connection: BaseVectorStoreConnection) -> None:
        """Make a live connection available to get_store().

        Args:
            provider: Provider the connection talks to
            connection: Connected instance
        """
        cls._connections[provider] = connection

    @classmethod
    def get_connection(cls, provider: Optional[VectorStoreType] = None) -> BaseVectorStoreConnection:
        """Fetch the registered connection for a provider.

        Args:
            provider: Provider to resolve; defaults to VECTOR_STORE_PROVIDER

        Returns:
            BaseVectorStoreConnection: The registered connection

        Raises:
            RuntimeError: If startup never registered a connection for this provider
        """
        provider = provider or cls.default_provider()
        connection = cls._connections.get(provider)
        if connection is None:
            raise RuntimeError(
                f"Vector store provider '{provider}' is not initialised. "
                f"Add it to VECTOR_STORE_PROVIDERS so startup connects it, "
                f"or the vector store was created with a provider this deployment no longer runs."
            )
        return connection

    @classmethod
    def get_store(cls,
                  collection_name: str,
                  provider: Optional[VectorStoreType] = None) -> BaseAsyncVectorStore:
        """Build a vector store bound to one collection.

        Args:
            collection_name: Collection to operate on (the vector store id)
            provider: Backend holding the collection. Pass the value stored on
                the vector store record so existing collections keep working
                after VECTOR_STORE_PROVIDER changes; omit it when creating one.

        Returns:
            BaseAsyncVectorStore: Store bound to collection_name

        Raises:
            ValueError: If the provider has no registered store class
            RuntimeError: If no connection is registered for the provider
        """
        provider = VectorStoreType(provider) if provider else cls.default_provider()

        backend = cls._backend(provider)
        store_cls = getattr(import_module(backend.module), backend.store_class)
        connection = cls.get_connection(provider)
        return store_cls(collection_name=collection_name, client=connection.client)

    @classmethod
    async def close_all(cls) -> None:
        """Close every registered connection. Used on shutdown."""
        for connection in cls._connections.values():
            await connection.close()
        cls._connections.clear()