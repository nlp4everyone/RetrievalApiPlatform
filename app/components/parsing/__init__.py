"""
Document parsing backends behind a single ParsingService facade.

Providers are grouped by the format they handle (provider/pdf, provider/unstructured).
ParsingService.from_settings() maps each file extension to a provider, using
PDF_PARSER_PROVIDER to pick between PDF backends. Every non-PDF format goes
through UnstructuredProvider, so output is Markdown across the board.
"""

from .base import BaseParsingProvider
from .provider import LlamaParseProvider, UnstructuredProvider
from .parsing_service import ParsingService