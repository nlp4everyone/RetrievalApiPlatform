# Inherit
from ..base import BaseChunkingProvider
# Text Splitter
from langchain_text_splitters import (CharacterTextSplitter,
                                      RecursiveCharacterTextSplitter,
                                      TextSplitter)
# Components
from langchain_core.documents import Document
# Local imports
from app.schemas.chunking import (ChunkingStrategy,
                                  LangchainChunkingConfig)
# Typing
from typing import List, Optional
# Other component
import asyncio


class LangchainProvider(BaseChunkingProvider):
    """Split text using LangChain's text splitters."""

    def __init__(self, config: Optional[LangchainChunkingConfig] = None) -> None:
        """
        Configure the LangChain splitter.

        Args:
            config (Optional[LangchainChunkingConfig]): Chunking configuration.
                Uses defaults if not provided.

        Raises:
            ValueError: If the configured strategy has no LangChain splitter
        """
        self.config = config or LangchainChunkingConfig()
        self._splitter = self._create_splitter()

    def _create_splitter(self) -> TextSplitter:
        """Create the appropriate text splitter based on configuration."""
        common_args = {'chunk_size': self.config.chunk_size,
                       'chunk_overlap': self.config.chunk_overlap}

        if self.config.strategy == ChunkingStrategy.CHARACTER:
            # Character splitter
            return CharacterTextSplitter(separator = self.config.separator,
                                         **common_args)
        elif self.config.strategy == ChunkingStrategy.RECURSIVE:
            # Recursive splitter
            return RecursiveCharacterTextSplitter(separators = self.config.separators,
                                                  **common_args)
        raise ValueError(f"LangChain has no splitter for strategy: {self.config.strategy}")

    @property
    def strategy_name(self) -> str:
        return f"langchain:{self.config.strategy.value}"

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
        return await asyncio.to_thread(self._splitter.split_text, text)

    async def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        Split a list of documents into chunked documents.

        Not part of BaseChunkingProvider - LangChain-specific, used where
        document metadata has to survive splitting.

        Args:
            documents (List[Document]): Documents to split

        Returns:
            List[Document]: Chunked documents
        """
        return await asyncio.to_thread(self._splitter.split_documents, documents)