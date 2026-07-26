"""
Dense embedding backends behind a single EmbeddingService facade.

EMBEDDING_PROVIDER ("openai" or "tei") picks which BaseEmbeddingProvider
implementation EmbeddingService.from_settings() builds.
"""

from .base import BaseEmbeddingProvider
from .provider.openai_provider import OpenAIEmbeddingProvider
from .provider.tei_provider import TEIEmbeddingProvider
from .embedding_service import EmbeddingService