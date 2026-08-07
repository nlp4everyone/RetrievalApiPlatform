"""
Dense and sparse embedding backends behind two service facades.

EMBEDDING_PROVIDER ("openai" or "tei") picks which BaseEmbeddingProvider
implementation EmbeddingService.from_settings() builds.

SPARSE_EMBEDDING_PROVIDER ("vllm") picks which BaseSparseEmbeddingProvider
implementation SparseEmbeddingService.from_settings() builds. Sparse embedding
is opt-in via SPARSE_EMBEDDING_ENABLED - dense is what every vector store is
served by today.
"""

from .base import BaseEmbeddingProvider, BaseSparseEmbeddingProvider, SparseVector
from .provider.openai_provider import OpenAIEmbeddingProvider
from .provider.tei_provider import TEIEmbeddingProvider
from .provider.vllm_sparse_provider import VLLMSparseEmbeddingProvider
from .embedding_service import EmbeddingService
from .sparse_embedding_service import SparseEmbeddingService