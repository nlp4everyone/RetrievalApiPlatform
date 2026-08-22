# Inherit
from ...base import BaseParsingProvider
# Main component
from llama_parse import LlamaParse
# Object
from llama_index.core.schema import Document
from llama_cloud_services.parse.utils import ResultType
# Utils
from app.utils.io import async_temp_file
# Typing
from typing import Any, ClassVar, List, Optional


class LlamaParseProvider(BaseParsingProvider):
    """Parse PDFs through the hosted LlamaParse service, returning Markdown."""

    name: ClassVar[str] = "llamaparse"

    def __init__(self,
                 api_key: Optional[str],
                 num_worker: int = 1,
                 result_type: ResultType = ResultType.MD,
                 verbose: bool = True,
                 **kwargs: Any) -> None:
        """
        Configure the LlamaParse client.

        Args:
            api_key (Optional[str]): API key for the LlamaParse service
            num_worker (int): Worker processes LlamaParse may use (default: 1)
            result_type (ResultType): Output format; Markdown by default
            verbose (bool): Enable LlamaParse's own logging (default: True)
            **kwargs: Additional keyword arguments forwarded to LlamaParse

        Raises:
            ValueError: If no API key is configured
        """
        # Checked here rather than at import time: a deployment that only ever
        # ingests text files should not fail to start over a missing PDF key
        if not api_key:
            raise ValueError("LLAMAPARSE_API_KEY must be set to parse PDF files")

        self._api_key = api_key
        self._num_worker = num_worker

        # Initialize LlamaParse parser with specified configuration
        self._parser = LlamaParse(api_key = self._api_key,
                                  result_type = result_type,
                                  num_workers = self._num_worker,
                                  verbose = verbose,
                                  **kwargs)

    async def parse(self, file_bytes: bytes, file_extension: str = "") -> str:
        """
        Parse a PDF and return its text content as Markdown.

        Args:
            file_bytes (bytes): Raw contents of the PDF

        Returns:
            str: Extracted content, pages joined by newlines

        Raises:
            Exception: If LlamaParse rejects the document or is unreachable
        """
        # Write to a temp file since LlamaParse expects a file path
        async with async_temp_file(file_bytes, suffix=".pdf") as path:
            # Parse document asynchronously using LlamaParse service
            documents: List[Document] = await self._parser.aload_data(path)

        # Extract text content from all documents and join with newlines
        return "\n".join([doc.text_resource.text for doc in documents])