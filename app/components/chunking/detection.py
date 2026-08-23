"""Pick a chunking strategy from the document itself, when the caller didn't.

Runs in the worker, after parsing: at create time the request carries file ids
and no content, so there is nothing to look at yet.

Imports no chunking library - it reads text and returns an enum value.
"""
import re

from app.schemas.chunking import ChunkingStrategy

# Only h1-h4, matching MarkdownChunkingConfig.headers_to_split_on. The
# Unstructured provider can emit deeper levels (it renders a Title at depth d
# as '#' * min(d + 1, 6)), and counting those would route a document to the
# markdown splitter that has no heading it can actually cut on.
_HEADING = re.compile(r"^#{1,4}\s", re.MULTILINE)

# One heading is a document title, not structure: the markdown splitter would
# make a single section and hand all of it to the recursive body splitter -
# an extra pass for the same result recursive gives directly.
_MIN_HEADINGS = 2

_MARKDOWN_EXTENSIONS = {".md", ".markdown"}


def detect_strategy(text: str, file_extension: str) -> ChunkingStrategy:
    """
    Choose a splitter for one document.

    Every parsing provider in this app returns Markdown, so the extension only
    settles the case where the upload was already Markdown; for everything else
    the heading count is the signal that there is structure worth splitting on.

    Args:
        text (str): Parsed document text
        file_extension (str): Lower-cased extension including the dot, e.g. ".pdf"

    Returns:
        ChunkingStrategy: MARKDOWN when the document has headings to split on,
            RECURSIVE otherwise
    """
    if file_extension in _MARKDOWN_EXTENSIONS:
        return ChunkingStrategy.MARKDOWN
    if len(_HEADING.findall(text)) >= _MIN_HEADINGS:
        return ChunkingStrategy.MARKDOWN
    return ChunkingStrategy.RECURSIVE
