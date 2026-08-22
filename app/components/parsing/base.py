from abc import ABC, abstractmethod
from typing import ClassVar

class BaseParsingProvider(ABC):
    """
    Interface for a document parsing backend.

    Concrete providers wrap a specific way of turning file bytes into text
    (an in-process decode, a call out to LlamaParse, ...). ParsingService
    depends only on this interface, so a new backend can be added without
    touching the ingestion pipeline or its callers.
    """

    # Stable slug identifying this backend. It is part of the parse cache key
    # (app.pipelines.ingestion.parsed_cache), so changing it invalidates every
    # artifact this provider produced - which is the correct behaviour when the
    # backend itself changes, and a needless re-parse otherwise. Not the class
    # name: renaming a class must not silently drop the cache.
    name: ClassVar[str]

    @abstractmethod
    async def parse(self, file_bytes: bytes, file_extension: str = "") -> str:
        """
        Extract text content from raw file bytes.

        Args:
            file_bytes (bytes): Raw contents of the file
            file_extension (str): Extension including the dot, e.g. ".docx".
                Providers that only ever handle one format may ignore it;
                multi-format providers use it as a content-type hint.

        Returns:
            str: Extracted text

        Raises:
            Exception: If the file cannot be parsed or the backend is unreachable
        """
        pass