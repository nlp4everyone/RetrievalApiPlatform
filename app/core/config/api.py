from .settings import settings

# API Config
API_VERSION = settings.API_VERSION
FASTAPI_API_KEY = settings.FASTAPI_API_KEY

# External API Keys
LLAMAPARSE_API_KEY = settings.LLAMAPARSE_API_KEY
UNSTRUCTURED_API_KEY = settings.UNSTRUCTURED_API_KEY
UNSTRUCTURED_API_URL = settings.UNSTRUCTURED_API_URL

# Parsing provider selection ("llamaparse")
PDF_PARSER_PROVIDER = settings.PDF_PARSER_PROVIDER