"""Assemble the retrieval pipeline for a given search type.

The search type is a per-call argument, not configuration: two queries against
the same vector store can reasonably want different retrieval, so the caller
decides. Callers that have no opinion ask resolve_search_type() instead, which
answers from what the target collection actually holds.
"""
from typing import List, NamedTuple, Optional

from app.core.config import SPARSE_EMBEDDING_ENABLED
from app.db.vector_store import BaseAsyncVectorStore
from app.pipelines.retrieval.fusion import BaseFusion, PassthroughFusion
from app.pipelines.retrieval.pipeline import RetrievalPipeline
from app.pipelines.retrieval.retriever import BaseRetriever, DenseRetriever, HybridRetriever
from app.pipelines.retrieval.stages import EmbedQueryStage, FuseStage, RetrieveStage
from app.pipelines.retrieval.stages.embed_query_stage import EmbedFn, SparseEmbedFn
from app.schemas.vector_store.types import SearchType


class _RetrievalPlan(NamedTuple):
    """What a search type resolves to: which retrievers, merged how."""
    retrievers: List[BaseRetriever]
    fusion: BaseFusion


async def hybrid_unavailable_reason(vector_store: BaseAsyncVectorStore,
                                    collection_exists: Optional[bool] = None) -> Optional[str]:
    """Explain why this collection cannot answer a hybrid search.

    Hybrid needs both halves to be real: a sparse embedding service live in
    this process (SPARSE_EMBEDDING_ENABLED) *and* a collection carrying sparse
    vectors. The second is not implied by the first - a store ingested before
    sparse was switched on has no sparse field, and Qdrant cannot add one to a
    live collection - so the collection is asked rather than assumed.

    A reason rather than a bool because the two failures need different actions
    from the caller: one is a server that was never configured for sparse, the
    other a collection that has to be re-ingested. resolve_search_type() and the
    error shown to a caller who asked for hybrid explicitly both read this, so
    the answer and the explanation cannot drift apart.

    Args:
        vector_store: Store bound to the collection about to be searched
        collection_exists: Pass an already-known collection_exists() result so
            supports_sparse() does not repeat that round-trip. None (the
            default) leaves it to make that call itself.

    Returns:
        Optional[str]: Why hybrid is unavailable, or None when it is available
    """
    if not SPARSE_EMBEDDING_ENABLED:
        return "sparse embedding is not enabled on this server"
    if not await vector_store.supports_sparse(collection_exists = collection_exists):
        return ("this vector store holds no sparse vectors - it was ingested "
                "before sparse embedding was enabled, and a collection cannot "
                "gain a sparse field after creation")
    return None


async def resolve_search_type(vector_store: BaseAsyncVectorStore,
                              collection_exists: Optional[bool] = None) -> SearchType:
    """Pick the best search this collection can actually answer.

    Resolving per search rather than per deployment is what lets old dense-only
    stores and new hybrid ones be served side by side by the same process.

    Args:
        vector_store: Store bound to the collection about to be searched
        collection_exists: Pass an already-known collection_exists() result so
            the hybrid check does not repeat that round-trip.

    Returns:
        SearchType: HYBRID when the collection can answer one, DENSE otherwise
    """
    if await hybrid_unavailable_reason(vector_store, collection_exists = collection_exists) is None:
        return SearchType.HYBRID
    return SearchType.DENSE


def _build_plan(search_type: SearchType,
                vector_store: BaseAsyncVectorStore,
                collection_exists: Optional[bool] = None) -> _RetrievalPlan:
    """Resolve a search type into retrievers plus the fusion that matches them.

    Retrievers and fusion are chosen together here so a combination that would
    drop results - several retrievers with a passthrough fusion - cannot be
    assembled by accident.

    Hybrid is a single retriever, not two: the vector store fuses the dense and
    sparse branches by rank internally (see AsyncQdrantVectorStore.retrieve), so
    only one candidate list ever reaches the fuse stage.

    Args:
        search_type: How the query should be answered
        vector_store: Store bound to the collection being searched
        collection_exists: Pass an already-known collection_exists() result so
            the retriever does not repeat that round-trip on its own.

    Returns:
        _RetrievalPlan: Retrievers to run and the strategy merging their hits

    Raises:
        ValueError: If the search type has no implementation
    """
    if search_type == SearchType.DENSE:
        return _RetrievalPlan(retrievers = [DenseRetriever(vector_store = vector_store,
                                                            known_collection_exists = collection_exists)],
                              fusion = PassthroughFusion())

    if search_type == SearchType.HYBRID:
        return _RetrievalPlan(retrievers = [HybridRetriever(vector_store = vector_store,
                                                             known_collection_exists = collection_exists)],
                              fusion = PassthroughFusion())

    raise ValueError(f"Unsupported search type: {search_type!r}")


def build_retrieval_pipeline(vector_store: BaseAsyncVectorStore,
                             embed_fn: EmbedFn,
                             search_type: SearchType = SearchType.DENSE,
                             sparse_embed_fn: Optional[SparseEmbedFn] = None,
                             collection_exists: Optional[bool] = None) -> RetrievalPipeline:
    """Build the embed_query -> retrieve -> fuse pipeline for one search type.

    Args:
        vector_store: Store bound to the collection being searched
        embed_fn: Coroutine turning texts into dense vectors
        search_type: How the query should be answered; dense by default
        sparse_embed_fn: Coroutine turning texts into sparse vectors; required
            for a hybrid search, ignored for a dense one
        collection_exists: Pass an already-known collection_exists() result
            (e.g. from resolving search_type) so the retriever built here does
            not make that same call again. None (the default) leaves it to
            the retriever to check for itself.

    Returns:
        RetrievalPipeline: Pipeline ready to run against a RetrievalContext

    Raises:
        ValueError: If the search type has no implementation, or if a hybrid
            search was asked for without a sparse embedder to answer it
    """
    plan = _build_plan(search_type, vector_store, collection_exists = collection_exists)

    hybrid = search_type == SearchType.HYBRID
    if hybrid and sparse_embed_fn is None:
        raise ValueError("Hybrid search needs a sparse_embed_fn - resolve_search_type() "
                         "only returns HYBRID when sparse embedding is enabled")

    return RetrievalPipeline(stages = [
        # The sparse embedder is withheld on a dense search so the query is not
        # embedded twice for a representation nothing will read
        EmbedQueryStage(embed_fn = embed_fn,
                        sparse_embed_fn = sparse_embed_fn if hybrid else None),
        RetrieveStage(retrievers = plan.retrievers),
        FuseStage(fusion = plan.fusion),
    ])