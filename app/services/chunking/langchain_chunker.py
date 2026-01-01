# Typing
from typing import List, Optional
from pydantic import BaseModel, Field
# Text Splitter
from langchain_text_splitters import (CharacterTextSplitter,
                                      RecursiveCharacterTextSplitter,
                                      TextSplitter)
# Components
from langchain_core.documents import Document

# Local imports
from app.schemas.chunking import (ChunkingStrategy,
                                  LangchainChunkingConfig)

class LangchainChunkingService:
    """Service for splitting text into chunks."""
    
    def __init__(self, config: Optional[LangchainChunkingConfig] = None):
        """Initialize with optional configuration.
        
        Args:
            config: Chunking configuration. Uses defaults if not provided.
        """
        self.config = config or LangchainChunkingConfig()
        self._splitter = self._create_splitter()
    
    def _create_splitter(self) -> TextSplitter:
        """Create the appropriate text splitter based on configuration."""
        common_args = {'chunk_size': self.config.chunk_size,
                       'chunk_overlap': self.config.chunk_overlap}
        
        if self.config.strategy == ChunkingStrategy.CHARACTER:
            # Token chunker
            return CharacterTextSplitter(separator = self.config.separator,
                                         **common_args)
        elif self.config.strategy == ChunkingStrategy.RECURSIVE:
            # Recursive chunker
            return RecursiveCharacterTextSplitter(separators = self.config.separators,
                                                  **common_args)
        else:
            raise Exception("Not support this kind of text splitter")
    
    def split_text(self, text: str) -> List[str]:
        """Split text into chunks.
        
        Args:
            text: The text to split
            
        Returns:
            List of text chunks
        """
        return self._splitter.split_text(text)
    
    def split_documents(self, documents: List[Document]) -> List[Document]:
        """Split a list of documents into chunks.
        
        Args:
            documents: List of documents, where each document is a dict with a 'text' key
            
        Returns:
            List of chunked documents
        """
        return self._splitter.split_documents(documents)

