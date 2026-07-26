"""Vector database backends, one package per engine.

Each implements BaseVectorStoreConnection (the startup connection) and
BaseAsyncVectorStore (per-collection operations) from app.db.vector_store.base,
plus a filter translator rendering the neutral filter into its own language.

Resolved lazily by VectorStoreFactory via module path, so a backend whose SDK
is not installed only fails when VECTOR_STORE_PROVIDER actually selects it.
"""