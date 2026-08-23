# Inherit
from ..base import BaseChunkingProvider
# Chonkie Chunking
from chonkie import (RecursiveChunker,
                     TokenChunker,
                     SentenceChunker)
from chonkie.chunker.base import BaseChunker
# Local imports
from app.schemas.chunking.chunking_config import (RecursiveChunkingConfig,
                                                  SentenceChunkingConfig,
                                                  TokenChunkingConfig)
# Typing
from typing import List
# Other component
import asyncio
from app.startup import get_cpu_executor


class ChonkieProvider(BaseChunkingProvider):
    """Run an already-built Chonkie chunker off the event loop.

    Which chunker it wraps is decided by the build_* functions below, one per
    strategy, so this class has no idea which strategy it is serving and no
    branch to keep in step with the registry.
    """

    def __init__(self, chunker: BaseChunker) -> None:
        """
        Args:
            chunker (BaseChunker): Configured Chonkie chunker to run
        """
        self._chunker = chunker

    async def split_text(self, text: str) -> List[str]:
        """
        Split text into chunks.

        Runs on a worker thread: splitting is CPU-bound and would otherwise
        stall the event loop for every other task in this process.

        Args:
            text (str): Text to split

        Returns:
            List[str]: Chunks in document order
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(get_cpu_executor(), self._split_sync, text)

    def _split_sync(self, text: str) -> List[str]:
        return [chunk.text for chunk in self._chunker.chunk(text)]


def build_recursive(config: RecursiveChunkingConfig) -> ChonkieProvider:
    """
    Build the recursive splitter: descends a hierarchy of delimiters.

    Args:
        config (RecursiveChunkingConfig): Recursive splitter settings

    Returns:
        ChonkieProvider: Provider wrapping a RecursiveChunker

    Note:
        RecursiveChunker takes no chunk_overlap - its constructor has no such
        parameter - which is what ChunkingStrategy.supports_overlap reports.
    """
    return ChonkieProvider(RecursiveChunker(tokenizer = config.tokenizer,
                                            chunk_size = config.chunk_size,
                                            rules = config.rules,
                                            min_characters_per_chunk = config.min_characters_per_chunk))


def build_token(config: TokenChunkingConfig) -> ChonkieProvider:
    """
    Build the token splitter: fixed-length windows with overlap.

    Args:
        config (TokenChunkingConfig): Token splitter settings

    Returns:
        ChonkieProvider: Provider wrapping a TokenChunker
    """
    return ChonkieProvider(TokenChunker(tokenizer = config.tokenizer,
                                        chunk_size = config.chunk_size,
                                        chunk_overlap = config.chunk_overlap))


def build_sentence(config: SentenceChunkingConfig) -> ChonkieProvider:
    """
    Build the sentence splitter: packs whole sentences up to chunk_size.

    Args:
        config (SentenceChunkingConfig): Sentence splitter settings

    Returns:
        ChonkieProvider: Provider wrapping a SentenceChunker
    """
    return ChonkieProvider(SentenceChunker(tokenizer = config.tokenizer,
                                           chunk_size = config.chunk_size,
                                           chunk_overlap = config.chunk_overlap,
                                           min_sentences_per_chunk = config.min_sentences_per_chunk,
                                           min_characters_per_sentence = config.min_characters_per_sentence))
