# Inherit
from ..base import BaseChunkingProvider
# Text Splitter
from langchain_text_splitters import (CharacterTextSplitter,
                                      MarkdownHeaderTextSplitter,
                                      RecursiveCharacterTextSplitter,
                                      TextSplitter)
# Local imports
from app.schemas.chunking.chunking_config import (CharacterChunkingConfig,
                                                  MarkdownChunkingConfig)
# Typing
from typing import Dict, List, Union
# Other component
import asyncio
from app.startup import get_cpu_executor


class LangchainProvider(BaseChunkingProvider):
    """Run an already-built LangChain-style splitter off the event loop.

    Anything with a `split_text(str) -> List[str]` will do, which is what lets
    the heading-aware Markdown splitter below sit here beside LangChain's own
    splitters without pretending to be a TextSplitter.
    """

    def __init__(self, splitter: Union[TextSplitter, "_MarkdownHeadingSplitter"]) -> None:
        """
        Args:
            splitter: Configured splitter exposing split_text()
        """
        self._splitter = splitter

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
        return await loop.run_in_executor(get_cpu_executor(), self._splitter.split_text, text)


def build_character(config: CharacterChunkingConfig) -> LangchainProvider:
    """
    Build the character splitter: cuts on one separator.

    Args:
        config (CharacterChunkingConfig): Character splitter settings

    Returns:
        LangchainProvider: Provider wrapping a CharacterTextSplitter
    """
    return LangchainProvider(CharacterTextSplitter(separator = config.separator,
                                                   chunk_size = config.chunk_size,
                                                   chunk_overlap = config.chunk_overlap))


def build_markdown(config: MarkdownChunkingConfig) -> LangchainProvider:
    """
    Build the heading-aware Markdown splitter.

    Args:
        config (MarkdownChunkingConfig): Markdown splitter settings

    Returns:
        LangchainProvider: Provider wrapping a _MarkdownHeadingSplitter
    """
    return LangchainProvider(_MarkdownHeadingSplitter(config))


class _MarkdownHeadingSplitter:
    """Split Markdown on its headings, prefixing each chunk with its heading path.

    Every parsing provider in this app returns Markdown, so headings are the
    only document structure that survives parsing - and they are exactly what a
    character-counting splitter throws away. This splits on them first, then
    cuts any section still over chunk_size with the recursive splitter.

    Each chunk carries the headings it sits under ("Guide > Billing > Refunds"),
    which is what makes an isolated fragment retrievable: a bare list of bullets
    matches almost no query, while the same bullets under their heading path
    carry the subject the query is asking about.

    Deliberately not a `TextSplitter` subclass: that contract is one splitter
    with one chunk size, and this is two passes whose second budget depends on
    what the first pass found. It only implements `split_text`, which is all
    `LangchainProvider` asks of it.
    """

    def __init__(self, config: MarkdownChunkingConfig) -> None:
        """
        Args:
            config: Markdown splitter settings
        """
        self._config = config
        # strip_headers: the heading line comes back as the path prefix, so
        # keeping it here too would put it in the chunk twice
        self._header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on = config.headers_to_split_on,
            strip_headers = True)
        # One splitter per body budget: the budget varies with the length of a
        # section's heading path, and rebuilding one per section is pure
        # overhead when documents repeat the same few heading depths
        self._body_splitters: Dict[int, RecursiveCharacterTextSplitter] = {}

    def split_text(self, text: str) -> List[str]:
        """
        Split Markdown into chunks, each carrying its heading path.

        Args:
            text (str): Markdown to split

        Returns:
            List[str]: Chunks in document order
        """
        if not text.strip():
            return []

        chunks: List[str] = []
        for section in self._header_splitter.split_text(text):
            prefix = self._heading_prefix(section.metadata)
            # The prefix rides on every chunk, so it has to come out of the
            # budget - otherwise chunk_size is quietly exceeded by its length
            budget = max(self._config.chunk_size - len(prefix), self._config.min_body_chars)
            for body in self._body_splitter(budget).split_text(section.page_content):
                chunks.append(prefix + body)
        return [chunk for chunk in chunks if chunk.strip()]

    def _heading_prefix(self, metadata: dict) -> str:
        """
        Render a section's headings as the prefix every chunk of it carries.

        Args:
            metadata: Header values keyed by the names in headers_to_split_on

        Returns:
            str: "Guide > Billing\n\n", or "" for text above the first heading
        """
        # Ordered by headers_to_split_on rather than by the dict, so the path
        # always reads outermost-first however the splitter filled it in
        path = [metadata[key] for _, key in self._config.headers_to_split_on
                if metadata.get(key)]
        if not path:
            return ""
        return self._config.path_separator.join(path) + "\n\n"

    def _body_splitter(self, budget: int) -> RecursiveCharacterTextSplitter:
        """
        Get the splitter for one body budget, building it on first use.

        Args:
            budget: Characters left for a chunk's own text

        Returns:
            RecursiveCharacterTextSplitter: Splitter for that budget
        """
        if budget not in self._body_splitters:
            # Overlap must stay under the budget or the splitter never advances
            overlap = min(self._config.chunk_overlap, budget // 2)
            self._body_splitters[budget] = RecursiveCharacterTextSplitter(
                chunk_size = budget,
                chunk_overlap = overlap,
                separators = self._config.separators)
        return self._body_splitters[budget]
