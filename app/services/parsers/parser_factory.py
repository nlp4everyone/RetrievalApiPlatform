# Inherit
from .base import BaseTextParser
# Parser
from .text_parser import AsyncTextParser
from .pdf import UnDatasIOPDFParser
# Schema
from app.schemas.file.types import FileFormat
# Typing
from typing import Tuple, Union
# Config
from app.core.config.service_params import UNDATASIO_API_KEY

class ParserFactory:
    @staticmethod
    def get(file_type: str) -> Union[Tuple[FileFormat,BaseTextParser],Tuple[None,None]]:
        file_type = file_type.lower()
        # Apply case
        if file_type in (".txt", ".md"):
            # Text format
            return FileFormat.TEXT, AsyncTextParser()
        elif file_type == ".pdf":
            # PDF format
            return FileFormat.PDF, UnDatasIOPDFParser(api_key = UNDATASIO_API_KEY)
        # elif file_type == "docx":
        #     return DocxParser()
        else:
            return None,None
