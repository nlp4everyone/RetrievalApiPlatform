# Typing
from typing import Any, Callable, Union, List
from pydantic import BaseModel, Field
# Chonkie Chunking
from chonkie import RecursiveRules
# Local imports
from app.schemas.chunking import ChunkingStrategy

# Default value
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 400

class ChonkieChunkingConfig(BaseModel):
    """Configuration for text chunking using Chonkie's RecursiveChunker.

    Attributes:
        strategy: The chunking strategy to use (character or recursive)
        chunk_size: Maximum size of chunks to create (in tokens)
        min_characters_per_chunk: Minimum number of characters per chunk
        tokenizer: Tokenizer to use (can be 'character' or a callable)
        rules: RecursiveRules configuration for chunking
    """
    strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    min_characters_per_chunk: int = 24
    min_sentences_per_chunk: int = 1
    min_characters_per_sentence: int = 12
    tokenizer: Union[str, Callable, Any] = "character"
    rules: RecursiveRules = Field(default_factory=RecursiveRules)


class LangchainChunkingConfig(BaseModel):
    """Configuration for text chunking.

    Attributes:
        strategy: The chunking strategy to use (character or recursive)
        chunk_size: Maximum size of chunks to create
        chunk_overlap: How much overlap between chunks
        separator: Separator to use for splitting (for character strategy)
        separators: List of separators to use (for recursive strategy)
    """
    strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    separator: str = "\n\n"
    separators: List[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", " ", ""]
    )