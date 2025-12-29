from .base import BaseTextParser
from .text_parser import TextParser

class ParserFactory:
    @staticmethod
    def get(file_type: str) -> BaseTextParser:
        file_type = file_type.lower()

        if file_type in ("txt", "md"):
            return TextParser()

        # if file_type == "pdf":
        #     return PdfParser()
        #
        # if file_type == "docx":
        #     return DocxParser()