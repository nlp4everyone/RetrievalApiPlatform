from abc import ABC, abstractmethod

class BaseParsingProvider(ABC):
    """
    Interface for a document parsing backend.

    Concrete providers wrap a specific way of turning file bytes into text
    (an in-process decode, a call out to LlamaParse, ...). ParsingService
    depends only on this interface, so a new backend can be added without
    touching the ingestion pipeline or its callers.
    """

    @abstractmethod
    async def parse(self, file_bytes: bytes) -> str:
        """
        Extract text content from raw file bytes.

        Args:
            file_bytes (bytes): Raw contents of the file

        Returns:
            str: Extracted text

        Raises:
            Exception: If the file cannot be parsed or the backend is unreachable
        """
        pass