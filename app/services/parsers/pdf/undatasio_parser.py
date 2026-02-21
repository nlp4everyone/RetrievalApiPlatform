# Inherit
from ..base import BaseTextParser
# Main component
from langchain_undatasio import UnDatasIOLoader
# Config
from app.core.config import UNDATASIO_API_KEY

class UnDatasIOPDFParser(BaseTextParser):
    def __init__(self,
                 api_key :str = UNDATASIO_API_KEY):
        """Initialize the UnDatasIO PDF parser.
        
        Args:
            api_key (str): API key for UnDatasIO service. 
                          Defaults to UNDATASIO_API_KEY from config.
        """
        self._api_key = api_key

    async def parse(self,
                    file_input :str) -> str:
        """Parse PDF file and extract text content.
        
        This method uses async lazy loading to efficiently process PDF documents,
        extracting text content page by page and joining them with newlines.
        
        Args:
            file_input (str): Path to the PDF file to be parsed
        
        Returns:
            str: Extracted text content from all pages, joined by newlines
        
        Raises:
            Exception: If the UnDatasIO service fails to parse the PDF
        """
        # Define Parser with lazy loading support for efficient memory usage
        self._parser = UnDatasIOLoader(token = self._api_key,
                                       file_path = file_input)

        # Get content by pages using async generator for memory efficiency
        return "\n".join([doc.page_content async for doc in self._parser.alazy_load()])