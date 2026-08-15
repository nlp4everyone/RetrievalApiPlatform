# Inherit
from .base import BaseParsingProvider
from .provider.pdf import LlamaParseProvider
from .provider.unstructured import UnstructuredProvider
# Config
from app.core.config import (LLAMAPARSE_API_KEY, PDF_PARSER_PROVIDER,
                             UNSTRUCTURED_API_KEY, UNSTRUCTURED_API_URL)
# Typing
from functools import partial
from typing import Callable

# A zero-argument callable that constructs a provider
ProviderFactory = Callable[[], BaseParsingProvider]


class ParsingService:
    """
    Facade over the parsing providers, dispatching on file extension.

    Callers only ever talk to this class - which backend actually parses a
    given file is resolved once, from config, in from_settings().

    Two things differ from EmbeddingService. Embedding resolves a single
    provider for the whole application, while parsing resolves one per file
    format, so this holds a map. And providers are built on first use rather
    than up front: a deployment that only ingests text files should not fail
    to start because the PDF backend has no API key configured.
    """

    def __init__(self, provider_factories: dict[str, ProviderFactory]) -> None:
        """
        Wrap a set of provider factories, one per supported file extension.

        Args:
            provider_factories (dict[str, ProviderFactory]): File extension
                (including the dot) mapped to a callable building its provider.
                Extensions sharing the same factory object share one instance.
        """
        self._factories = {extension.lower(): factory
                           for extension, factory in provider_factories.items()}
        # Keyed by factory, not extension, so .txt and .md registered against
        # the same factory reuse a single provider instance
        self._instances: dict[ProviderFactory, BaseParsingProvider] = {}

    @property
    def supported_extensions(self) -> list[str]:
        """
        Extensions this service can parse.

        Returns:
            list[str]: Sorted list of supported extensions
        """
        return sorted(self._factories)

    def provider_for(self, file_extension: str) -> BaseParsingProvider:
        """
        Get the provider registered for a file extension, building it on first use.

        Args:
            file_extension (str): Extension including the dot, e.g. ".pdf"

        Returns:
            BaseParsingProvider: The provider that handles this format

        Raises:
            ValueError: If no provider is registered for the extension, or the
                provider is registered but misconfigured (e.g. missing API key)
        """
        factory = self._factories.get(file_extension.lower())
        if factory is None:
            raise ValueError(f"Unsupported file format: {file_extension}")

        if factory not in self._instances:
            self._instances[factory] = factory()
        return self._instances[factory]

    async def parse(self, file_bytes: bytes, file_extension: str) -> str:
        """
        Extract text from a file using the provider for its extension.

        Args:
            file_bytes (bytes): Raw contents of the file
            file_extension (str): Extension including the dot, e.g. ".pdf"

        Returns:
            str: Extracted text

        Raises:
            ValueError: If no provider is registered for the extension
        """
        return await self.provider_for(file_extension).parse(file_bytes, file_extension)

    @classmethod
    def from_settings(cls) -> "ParsingService":
        """
        Build the ParsingService for the providers selected via config.

        PDFs go to the backend named by PDF_PARSER_PROVIDER, which is
        validated here but not constructed until a PDF is actually parsed.
        Every other format - plain text, Markdown, Word documents, images -
        goes to the Unstructured API, converted to Markdown so all providers
        agree on output shape.

        Returns:
            ParsingService: Instance wrapping the configured provider factories

        Raises:
            ValueError: If PDF_PARSER_PROVIDER names a backend this app cannot build
        """
        pdf_factory = cls._pdf_provider_factory()
        unstructured_factory: ProviderFactory = partial(UnstructuredProvider,
                                                        api_key=UNSTRUCTURED_API_KEY,
                                                        api_url=UNSTRUCTURED_API_URL)

        return cls({".pdf": pdf_factory,
                    ".txt": unstructured_factory,
                    ".md": unstructured_factory,
                    ".docx": unstructured_factory,
                    ".doc": unstructured_factory,
                    ".png": unstructured_factory,
                    ".jpg": unstructured_factory,
                    ".jpeg": unstructured_factory})

    @staticmethod
    def _pdf_provider_factory() -> ProviderFactory:
        """
        Resolve the PDF backend named by PDF_PARSER_PROVIDER.

        The provider name is checked now so a typo fails at startup; the
        provider's own credentials are only checked when it is first used.

        Returns:
            ProviderFactory: Callable constructing the configured PDF provider

        Raises:
            ValueError: If PDF_PARSER_PROVIDER names a backend this app cannot build
        """
        if PDF_PARSER_PROVIDER == "llamaparse":
            return partial(LlamaParseProvider, api_key = LLAMAPARSE_API_KEY)
        raise ValueError(f"Unsupported PDF_PARSER_PROVIDER: {PDF_PARSER_PROVIDER!r}")