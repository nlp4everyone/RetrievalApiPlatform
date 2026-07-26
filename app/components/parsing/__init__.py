"""
Document parsing backends behind a single ParsingService facade.

Providers are grouped by the format they handle (provider/common, provider/pdf).
ParsingService.from_settings() maps each file extension to a provider, using
PDF_PARSER_PROVIDER to pick between PDF backends.
"""

from .base import BaseParsingProvider
from .provider import LlamaParseProvider, TextProvider
from .parsing_service import ParsingService