# Inherit
from .base import BaseTextParser
# Parser
from .text_parser import AsyncTextParser
from .pdf import UnDatasIOPDFParser, LlamaParseParser
# Schema
from app.schemas.file.types import FileFormat
# Typing
from typing import Tuple, Union

class ParserFactory:
    @staticmethod
    def get(file_type: str) -> Union[Tuple[FileFormat,BaseTextParser],Tuple[None,None]]:
        """Get appropriate parser for the given file type.
        
        Args:
            file_type (str): File extension including the dot (e.g., ".pdf", ".txt")
        
        Returns:
            Union[Tuple[FileFormat, BaseTextParser], Tuple[None, None]]: 
                - Tuple of (FileFormat, parser_instance) if supported
                - Tuple of (None, None) if file type is not supported
        
        Note:
            File type comparison is case-insensitive.
        """
        file_type = file_type.lower()
        # Apply case
        if file_type in (".txt", ".md"):
            # Text format - use async text parser for plain text and markdown files
            return FileFormat.TEXT, AsyncTextParser()
        elif file_type == ".pdf":
            # PDF format - use LlamaParse parser for PDF documents
            return FileFormat.PDF, LlamaParseParser()
        # elif file_type == "docx":
        #     return DocxParser()
        else:
            # Unsupported file type
            return None,None
