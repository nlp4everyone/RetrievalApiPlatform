# Inherit
from .base import BaseTextParser
# Parser
from .text_parser import AsyncTextParser
from .pdf import LlamaParseParser
# Typing
from typing import Dict, Optional, Type

class ParserFactory:
    _registry: Dict[str, Type[BaseTextParser]] = {
        ".txt": AsyncTextParser,
        ".md": AsyncTextParser,
        ".pdf": LlamaParseParser,
    }
    _instances: Dict[str, BaseTextParser] = {}

    @classmethod
    def get(cls, file_type: str) -> Optional[BaseTextParser]:
        """Get the parser instance registered for the given file type.

        Args:
            file_type (str): File extension including the dot (e.g., ".pdf", ".txt")

        Returns:
            Optional[BaseTextParser]: The parser instance if supported, else None.

        Note:
            File type comparison is case-insensitive. Parser instances are
            cached and reused across calls.
        """
        file_type = file_type.lower()
        parser_cls = cls._registry.get(file_type)
        if parser_cls is None:
            return None
        if file_type not in cls._instances:
            cls._instances[file_type] = parser_cls()
        return cls._instances[file_type]