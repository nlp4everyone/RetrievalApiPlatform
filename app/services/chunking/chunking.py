# Typing
from typing import List, Optional
from pydantic import BaseModel, Field
# Text Splitter
from langchain_text_splitters import (CharacterTextSplitter,
                                      RecursiveCharacterTextSplitter,
                                      TextSplitter)
# Components
from langchain_core.documents import Document
from enum import Enum


class ChunkingStrategy(str, Enum):
    """Available text chunking strategies."""
    CHARACTER = "character"
    RECURSIVE = "recursive"


class ChunkingConfig(BaseModel):
    """Configuration for text chunking.
    
    Attributes:
        strategy: The chunking strategy to use (character or recursive)
        chunk_size: Maximum size of chunks to create
        chunk_overlap: How much overlap between chunks
        separator: Separator to use for splitting (for character strategy)
        separators: List of separators to use (for recursive strategy)
    """
    strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE
    chunk_size: int = 1000
    chunk_overlap: int = 200
    separator: str = "\n\n"
    separators: List[str] = Field(
        default_factory=lambda: ["\n\n", "\n", ". ", " ", ""]
    )


class ChunkingService:
    """Service for splitting text into chunks."""
    
    def __init__(self, config: Optional[ChunkingConfig] = None):
        """Initialize with optional configuration.
        
        Args:
            config: Chunking configuration. Uses defaults if not provided.
        """
        self.config = config or ChunkingConfig()
        self._splitter = self._create_splitter()
    
    def _create_splitter(self) -> TextSplitter:
        """Create the appropriate text splitter based on configuration."""
        common_args = {'chunk_size': self.config.chunk_size,
                       'chunk_overlap': self.config.chunk_overlap}
        
        if self.config.strategy == ChunkingStrategy.CHARACTER:
            return CharacterTextSplitter(separator = self.config.separator,
                                         **common_args)
        else:  # RECURSIVE
            return RecursiveCharacterTextSplitter(separators = self.config.separators,
                                                  **common_args)
    
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

