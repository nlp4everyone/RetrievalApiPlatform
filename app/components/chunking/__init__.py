"""
Text chunking backends behind a single ChunkingService facade.

CHUNKING_PROVIDER ("chonkie" or "langchain") picks which BaseChunkingProvider
implementation ChunkingService.from_settings() builds.
"""

from .base import BaseChunkingProvider
from .provider import ChonkieProvider, LangchainProvider
from .chunking_service import ChunkingService