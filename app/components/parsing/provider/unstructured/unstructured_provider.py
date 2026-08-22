# Inherit
from ...base import BaseParsingProvider
# Main component
from unstructured.partition.api import partition_via_api
from unstructured.documents.elements import Element
from bs4 import BeautifulSoup
# Utils
import asyncio
# Typing
from typing import Any, ClassVar, List, Optional


class UnstructuredProvider(BaseParsingProvider):
    """Parse documents through the hosted Unstructured API, returning Markdown.

    The Unstructured API only supports application/json or text/csv response
    formats (no native Markdown output), so this provider requests the JSON
    element list and converts it to Markdown locally, to match the output of
    the other parsing providers (e.g. LlamaParseProvider for PDFs).
    """

    name: ClassVar[str] = "unstructured"

    def __init__(self,
                 api_key: Optional[str],
                 api_url: str = "https://api.unstructuredapp.io/general/v0/general",
                 strategy: str = "auto",
                 **request_kwargs: Any) -> None:
        """
        Configure the Unstructured API client.

        Args:
            api_key (Optional[str]): API key for the Unstructured API
            api_url (str): Endpoint to send documents to (default: hosted API)
            strategy (str): Partitioning strategy passed to the API (default: "auto")
            **request_kwargs: Additional keyword arguments forwarded to the
                Unstructured partition request (e.g. `languages`)

        Raises:
            ValueError: If no API key is configured
        """
        # Checked here rather than at import time: a deployment that never
        # ingests the formats routed to this provider should not fail to
        # start over a missing key
        if not api_key:
            raise ValueError("UNSTRUCTURED_API_KEY must be set to parse documents via Unstructured")

        self._api_key = api_key
        self._api_url = api_url
        self._request_kwargs = {"strategy": strategy, **request_kwargs}

    async def parse(self, file_bytes: bytes, file_extension: str = "") -> str:
        """
        Parse a document via the Unstructured API and return it as Markdown.

        Args:
            file_bytes (bytes): Raw contents of the file
            file_extension (str): Extension including the dot, e.g. ".docx".
                Passed along as a filename hint so the API can detect the
                document type.

        Returns:
            str: Extracted content, converted to Markdown

        Raises:
            Exception: If the Unstructured API rejects the document or is unreachable
        """
        elements: List[Element] = await asyncio.to_thread(
            partition_via_api,
            file=file_bytes,
            metadata_filename=f"document{file_extension}",
            api_key=self._api_key,
            api_url=self._api_url,
            **self._request_kwargs,
        )
        return _elements_to_markdown(elements)


def _elements_to_markdown(elements: List[Element]) -> str:
    """Join partitioned elements into a single Markdown document."""
    blocks = [_element_to_markdown(element) for element in elements]
    return "\n\n".join(block for block in blocks if block.strip())


def _element_to_markdown(element: Element) -> str:
    """Render one element as a Markdown block, based on its category."""
    category = element.category
    depth = getattr(element.metadata, "category_depth", None) or 0

    if category == "Title":
        return f"{'#' * min(depth + 1, 6)} {element.text}"
    if category == "ListItem":
        return f"{'  ' * depth}- {element.text}"
    if category == "Table":
        html = getattr(element.metadata, "text_as_html", None)
        return _table_to_markdown(html) if html else element.text
    return element.text


def _table_to_markdown(html: str) -> str:
    """Convert a Table element's `text_as_html` metadata into a Markdown table."""
    rows = [
        [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
        for row in BeautifulSoup(html, "html.parser").find_all("tr")
    ]
    rows = [row for row in rows if row]
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]

    header, *body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)
