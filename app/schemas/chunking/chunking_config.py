"""One config model per chunking strategy, each holding only its own knobs.

These were previously two "god configs" - one per library - that pooled the
knobs of every strategy, so a field like `separator` sat next to `rules` even
though no splitter reads both. Splitting them means adding a strategy adds a
model here rather than widening one every other strategy has to ignore.

Imported by the worker only: `chonkie` is pulled in below, so the API process
must reach the enum through `app.schemas.chunking` (which does not import this
module) rather than through here.
"""
# Typing
from typing import Any, Callable, Union, List, Tuple
from pydantic import BaseModel, Field
# Chonkie Chunking
from chonkie import RecursiveRules

# Default value
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 400

# Markdown splits on headings first, so its chunks are whole sections rather
# than fixed windows - and a section is bigger than a window. Measured on a
# parsed Wikipedia article: at 800 five of the twenty paragraphs had to be cut
# mid-text, leaving fragments as short as 59 characters that began mid-sentence;
# at 1200 only one did. And a 400 overlap against a ~780 character body budget
# is roughly 50%, which inflated the stored text by 26% - at 120 it is 1%.
# The cost is coarser retrieval granularity, which is why it is scoped to this
# strategy instead of raising the defaults for every splitter.
MARKDOWN_CHUNK_SIZE = 1200
MARKDOWN_CHUNK_OVERLAP = 120


class BaseChunkingConfig(BaseModel):
    """What every splitter needs: how big a chunk may get.

    Attributes:
        chunk_size: Maximum size of the chunks to create, in characters
            (every splitter here counts characters, not tokens)
    """
    chunk_size: int = DEFAULT_CHUNK_SIZE


class OverlapChunkingConfig(BaseChunkingConfig):
    """Base for the splitters that can repeat text across chunk boundaries.

    Deliberately not on BaseChunkingConfig: the recursive strategy has no
    overlap knob to set, and giving its config one would invite callers to
    pass a value that is silently dropped.

    Attributes:
        chunk_overlap: How much of each chunk is repeated in the next one
    """
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP


class RecursiveChunkingConfig(BaseChunkingConfig):
    """Chonkie's RecursiveChunker - splits down a hierarchy of delimiters.

    Attributes:
        tokenizer: How length is measured; "character" or a callable
        min_characters_per_chunk: Chunks shorter than this are merged away
        rules: The delimiter hierarchy to descend, outermost level first
    """
    tokenizer: Union[str, Callable, Any] = "character"
    min_characters_per_chunk: int = 24
    rules: RecursiveRules = Field(default_factory=RecursiveRules)


class TokenChunkingConfig(OverlapChunkingConfig):
    """Chonkie's TokenChunker - fixed-length windows with overlap.

    Attributes:
        tokenizer: How length is measured; "character" or a callable
    """
    tokenizer: Union[str, Callable, Any] = "character"


class SentenceChunkingConfig(OverlapChunkingConfig):
    """Chonkie's SentenceChunker - packs whole sentences up to chunk_size.

    Attributes:
        tokenizer: How length is measured; "character" or a callable
        min_sentences_per_chunk: Never emit a chunk with fewer sentences
        min_characters_per_sentence: Shorter fragments join the neighbouring sentence
    """
    tokenizer: Union[str, Callable, Any] = "character"
    min_sentences_per_chunk: int = 1
    min_characters_per_sentence: int = 12


class CharacterChunkingConfig(OverlapChunkingConfig):
    """LangChain's CharacterTextSplitter - cuts on a single separator.

    Attributes:
        separator: The one string splits are made on
    """
    separator: str = "\n\n"


class MarkdownChunkingConfig(OverlapChunkingConfig):
    """Heading-aware Markdown splitting, with a recursive pass for long sections.

    Attributes:
        separators: Fallbacks for sections still over chunk_size after the
            heading split, tried outermost first
        headers_to_split_on: Headings that open a section, outermost first,
            each paired with the metadata key its text is recorded under
        path_separator: Joins the heading path prefixed to a chunk
        min_body_chars: Minimum body room left after the prefix
    """
    # Wider and less overlapping than the shared defaults - see the constants
    chunk_size: int = MARKDOWN_CHUNK_SIZE
    chunk_overlap: int = MARKDOWN_CHUNK_OVERLAP
    # "  \n", not "\n\n": the heading splitter drops blank lines and rejoins
    # paragraphs with a Markdown hard break, so a blank line never survives
    # into this pass and "\n\n" would be a rung that never matches. Keeping
    # the paragraph boundary above the line boundary is what stops a chunk
    # from ending halfway through a multi-line block - a list, a table, a
    # numbered procedure - and starting the next one mid-item.
    separators: List[str] = Field(
        default_factory=lambda: ["  \n", "\n", ". ", " ", ""]
    )
    headers_to_split_on: List[Tuple[str, str]] = Field(
        default_factory=lambda: [("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")]
    )
    # Joins the heading path prefixed to every chunk of a section
    path_separator: str = " > "
    # Floor on the room left for a chunk's own text once the heading path has
    # taken its share of chunk_size - a deep path must not squeeze the body out
    min_body_chars: int = 120
