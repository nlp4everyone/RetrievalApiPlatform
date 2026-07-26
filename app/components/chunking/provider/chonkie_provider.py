# Inherit
from ..base import BaseChunkingProvider
# Chonkie Chunking
from chonkie import (RecursiveChunker,
                     TokenChunker,
                     SentenceChunker)
from chonkie.chunker.base import BaseChunker
# Local imports
from app.schemas.chunking import (ChunkingStrategy,
                                  ChonkieChunkingConfig)
# Typing
from typing import List, Optional
# Other component
import asyncio


class ChonkieProvider(BaseChunkingProvider):
    """Split text using Chonkie's chunkers."""

    def __init__(self, config: Optional[ChonkieChunkingConfig] = None) -> None:
        """
        Configure the Chonkie chunker.

        Args:
            config (Optional[ChonkieChunkingConfig]): Chunking configuration.
                Uses defaults if not provided.
        """
        self.config = config or ChonkieChunkingConfig()
        self._chunker = self._create_chunker()

    def _create_chunker(self) -> BaseChunker:
        """Create the Chonkie Chunker based on configuration."""
        if self.config.strategy == ChunkingStrategy.TOKEN:
            # Token chunker
            return TokenChunker(tokenizer = self.config.tokenizer,
                                chunk_size = self.config.chunk_size,
                                chunk_overlap = self.config.chunk_overlap)
        elif self.config.strategy == ChunkingStrategy.SENTENCE:
            # Sentence chunker
            return SentenceChunker(tokenizer = self.config.tokenizer,
                                   chunk_size = self.config.chunk_size,
                                   chunk_overlap = self.config.chunk_overlap,
                                   min_sentences_per_chunk = self.config.min_sentences_per_chunk,
                                   min_characters_per_sentence = self.config.min_characters_per_sentence)
        else:
            # Recursive chunker
            return RecursiveChunker(tokenizer = self.config.tokenizer,
                                    chunk_size = self.config.chunk_size,
                                    rules = self.config.rules,
                                    min_characters_per_chunk = self.config.min_characters_per_chunk)

    @property
    def strategy_name(self) -> str:
        return f"chonkie:{self.config.strategy.value}"

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
        return await asyncio.to_thread(self._split_sync, text)

    def _split_sync(self, text: str) -> List[str]:
        return [chunk.text for chunk in self._chunker.chunk(text)]