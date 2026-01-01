# Typing
from typing import List, Optional
# Chonkie Chunking
from chonkie import (RecursiveChunker,
                     TokenChunker,
                     SentenceChunker)
from chonkie.chunker.base import BaseChunker
# Local imports
from app.schemas.chunking import (ChunkingStrategy,
                                  ChonkieChunkingConfig)

class ChonkieChunkingService:
    """Service for splitting text into chunks using Chonkie's RecursiveChunker."""
    
    def __init__(self, config: Optional[ChonkieChunkingConfig] = None):
        """Initialize with optional configuration.
        
        Args:
            config: Chunking configuration. Uses defaults if not provided.
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
    
    def split_text(self, text: str) -> List[str]:
        """Split text into chunks.
        
        Args:
            text: The text to split
            
        Returns:
            List of text chunks
        """
        return [chunk.text for chunk in self._chunker.chunk(text)]

