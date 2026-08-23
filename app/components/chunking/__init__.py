"""
Text chunking backends behind a single ChunkingService facade.

The strategy picks the implementation - see .registry for the table. When a
request names no strategy, .detection reads one off the parsed document.
"""

from .base import BaseChunkingProvider
from .chunking_service import ChunkingService
from .detection import detect_strategy
from .registry import REGISTRY, ChunkerSpec
