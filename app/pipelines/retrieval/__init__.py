"""Query retrieval: embed_query -> retrieve -> fuse.

Dense or hybrid, decided per search by resolve_search_type() from what the
target collection holds. Hybrid fuses dense and sparse inside the vector store
(one round-trip, ranks merged by RRF), so it reaches the pipeline as a single
retriever. The retrieve stage still runs any number of retrievers concurrently
and the fuse stage still merges their lists through a swappable strategy - that
seam is what a backend unable to fuse server-side would need.
"""
from .base import BaseRetrievalStage
from .context import RetrievalContext
from .fusion import BaseFusion, PassthroughFusion
from .pipeline import RetrievalPipeline
from .retriever import BaseRetriever, DenseRetriever, HybridRetriever, RetrievalQuery
from .factory import build_retrieval_pipeline, hybrid_unavailable_reason, resolve_search_type
from app.schemas.vector_store.types import SearchType