"""Assemble the retrieval pipeline for a given search type.

The search type is a per-call argument, not configuration: two queries against
the same vector store can reasonably want different retrieval, so the caller
decides. It defaults to dense.

Enabling hybrid search is: implement a keyword/BM25 retriever and a real fusion
strategy, add SearchType.KEYWORD / SearchType.HYBRID, and give them a branch in
_build_plan() below. The stages and the pipeline need no change.
"""
from typing import List, NamedTuple

from app.db.vector_store import BaseAsyncVectorStore
from app.pipelines.retrieval.fusion import BaseFusion, PassthroughFusion
from app.pipelines.retrieval.pipeline import RetrievalPipeline
from app.pipelines.retrieval.retriever import BaseRetriever, DenseRetriever
from app.pipelines.retrieval.stages import EmbedQueryStage, FuseStage, RetrieveStage
from app.pipelines.retrieval.stages.embed_query_stage import EmbedFn
from app.schemas.vector_store.types import SearchType


class _RetrievalPlan(NamedTuple):
    """What a search type resolves to: which retrievers, merged how."""
    retrievers: List[BaseRetriever]
    fusion: BaseFusion


def _build_plan(search_type: SearchType,
                vector_store: BaseAsyncVectorStore) -> _RetrievalPlan:
    """Resolve a search type into retrievers plus the fusion that matches them.

    Retrievers and fusion are chosen together here so a combination that would
    drop results - several retrievers with a passthrough fusion - cannot be
    assembled by accident.

    Args:
        search_type: How the query should be answered
        vector_store: Store bound to the collection being searched

    Returns:
        _RetrievalPlan: Retrievers to run and the strategy merging their hits

    Raises:
        ValueError: If the search type has no implementation
    """
    if search_type == SearchType.DENSE:
        return _RetrievalPlan(retrievers = [DenseRetriever(vector_store = vector_store)],
                              fusion = PassthroughFusion())

    # SearchType.KEYWORD  -> [BM25Retriever(...)],               PassthroughFusion()
    # SearchType.HYBRID   -> [DenseRetriever(...), BM25Retriever(...)], ReciprocalRankFusion()
    raise ValueError(f"Unsupported search type: {search_type!r}")


def build_retrieval_pipeline(vector_store: BaseAsyncVectorStore,
                             embed_fn: EmbedFn,
                             search_type: SearchType = SearchType.DENSE) -> RetrievalPipeline:
    """Build the embed_query -> retrieve -> fuse pipeline for one search type.

    Args:
        vector_store: Store bound to the collection being searched
        embed_fn: Coroutine turning texts into dense vectors
        search_type: How the query should be answered; dense by default

    Returns:
        RetrievalPipeline: Pipeline ready to run against a RetrievalContext

    Raises:
        ValueError: If the search type has no implementation
    """
    plan = _build_plan(search_type, vector_store)

    return RetrievalPipeline(stages = [
        EmbedQueryStage(embed_fn = embed_fn),
        RetrieveStage(retrievers = plan.retrievers),
        FuseStage(fusion = plan.fusion),
    ])
