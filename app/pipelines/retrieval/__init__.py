"""Query retrieval: embed_query -> retrieve -> fuse.

Dense vector search only today. The shape anticipates hybrid search: the
retrieve stage runs any number of retrievers concurrently and keys their hits
by name, and the fuse stage merges those lists through a swappable strategy.
Adding BM25 therefore means adding a BaseRetriever and a BaseFusion, not
restructuring the pipeline.
"""
from .base import BaseRetrievalStage
from .context import RetrievalContext
from .fusion import BaseFusion, PassthroughFusion
from .pipeline import RetrievalPipeline
from .retriever import BaseRetriever, DenseRetriever, RetrievalQuery
from .factory import build_retrieval_pipeline
from app.schemas.vector_store.types import SearchType