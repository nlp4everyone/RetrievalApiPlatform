from enum import Enum

class ChunkingStrategy(str, Enum):
    """Available text chunking strategies."""
    CHARACTER = "character"
    SENTENCE = "sentence"
    RECURSIVE = "recursive"
    TOKEN = "token"
