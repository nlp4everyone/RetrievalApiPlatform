from enum import Enum

class ChunkingStrategy(str, Enum):
    """Available text chunking strategies.

    Each value names exactly one splitter implementation - see
    app.components.chunking.registry for the mapping. Adding a value here
    without adding its registry entry fails at import time.
    """
    CHARACTER = "character"
    SENTENCE = "sentence"
    RECURSIVE = "recursive"
    TOKEN = "token"
    MARKDOWN = "markdown"

    @property
    def supports_overlap(self) -> bool:
        """Whether this splitter has any notion of overlapping chunks.

        Lives on the enum rather than on the chunker classes so request
        validation can read it: app.schemas must not import
        app.components.chunking, which would pull chonkie and
        langchain-text-splitters into the API process just to check a flag.

        Returns:
            bool: False for recursive - Chonkie's RecursiveChunker splits down
                a hierarchy of delimiters and its constructor takes no
                chunk_overlap at all (verified against chonkie 1.4.2)
        """
        return self is not ChunkingStrategy.RECURSIVE
