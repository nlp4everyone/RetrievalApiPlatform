# Inherit
import logging

from ..base import BaseTextParser
# Main component
from llama_parse import LlamaParse
# Config
from app.core.config import LLAMAPARSE_API_KEY
# Object
from llama_index.core.schema import Document
from llama_cloud_services.parse.utils import ResultType
# Typing
from typing import List, Tuple

# Validate API key availability at import time
# This ensures configuration issues are caught early
if not LLAMAPARSE_API_KEY:
    raise ValueError("Llamaparse key must be provided!")


class LlamaParseParser(BaseTextParser):
    def __init__(self,
                 api_key :str = LLAMAPARSE_API_KEY,
                 num_worker :int = 1,
                 result_type :ResultType = ResultType.MD,
                 verbose :bool = True,
                 **kwargs):
        """Initialize the LlamaParse PDF parser.
        
        Args:
            api_key (str): API key for LlamaParse service. 
                          Defaults to LLAMAPARSE_API_KEY from config.
            num_worker (int): Number of worker processes for parallel processing.
                             Defaults to 1.
            result_type (ResultType): Output format type. Defaults to Markdown.
            verbose (bool): Enable verbose logging. Defaults to True.
            **kwargs: Additional keyword arguments passed to LlamaParse.
        
        Raises:
            ValueError: If API key is not provided or invalid.
        """
        # Store configuration parameters
        self._api_key = api_key
        self._num_worker = num_worker

        # Initialize LlamaParse parser with specified configuration
        self._parser = LlamaParse(api_key = self._api_key,
                                  result_type = result_type,
                                  num_workers = self._num_worker,
                                  verbose = verbose,
                                  **kwargs)

    async def parse(self,
                    file_input :str,
                    lazy_load :bool = False) -> str:
        """Parse PDF file and extract text content.
        
        This method processes PDF documents using LlamaParse service and returns
        the extracted text content in the specified format (default: Markdown).
        
        Args:
            file_input (str): Path to the PDF file to be parsed
            lazy_load (bool): Enable lazy loading mode. Currently not supported.
                           Defaults to False.
        
        Returns:
            str: Extracted text content from the PDF document
        """
        # Validate lazy loading parameter
        if lazy_load:
            raise ValueError("Not support lazy load")
            
        # Parse document asynchronously using LlamaParse service
        documents :List[Document] = await self._parser.aload_data(file_input)
        
        # Extract text content from all documents and join with newlines
        content = "\n".join([doc.text_resource.text for doc in documents])
        return content