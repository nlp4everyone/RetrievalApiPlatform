from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator, model_validator
from typing import Optional


# Settings each backend cannot connect without. Doubles as the allowed set for
# VECTOR_STORE_PROVIDER, so adding a backend is one edit.
_VECTOR_STORE_REQUIRED_SETTINGS: dict[str, tuple[str, ...]] = {
    "qdrant": ("QDRANT_URL", "QDRANT_API_KEY"),
    "milvus": ("MILVUS_URI",),
}


class Settings(BaseSettings):
    """Application settings with validation using pydantic-settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )
    
    # API Config
    API_VERSION: str = "v1"
    # Read by docker-compose (uvicorn --workers), not by application code
    NUM_WORKERS: int = 1
    FASTAPI_API_KEY: str = Field(..., description="FastAPI API key for authentication")

    # Logging Config
    LOG_LEVEL: str = Field("INFO", description="Minimum log level captured by SystemLogger")
    LOG_FORMAT: str = Field("auto", description="Log output format: 'auto' (detect TTY), 'console' (colorized text), or 'json' (structured)")
    
    # External API Keys
    LLAMAPARSE_API_KEY: Optional[str] = Field(None, description="LlamaParse API key")
    UNSTRUCTURED_API_KEY: Optional[str] = Field(None, description="Unstructured API key")
    UNSTRUCTURED_API_URL: str = "https://api.unstructuredapp.io/general/v0/general"

    # Parsing provider selection for PDFs. Every other format (.txt, .md,
    # .docx, .doc, images) is parsed via the Unstructured API, so only PDF
    # has a backend worth choosing.
    PDF_PARSER_PROVIDER: str = "llamaparse"
    
    # Model Config
    DENSE_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    SPARSE_MODEL_NAME: str = "BAAI/bge-m3"

    # Embedding provider selection: "openai" (OpenAI-compatible endpoint, e.g. vLLM)
    # or "tei" (raw HTTP request to a Text Embeddings Inference /embed endpoint).
    # Every provider reads the same endpoint/credential pair below.
    EMBEDDING_PROVIDER: str = "openai"
    DENSE_EMBEDDING_URL: str = "http://172.17.0.1:8100/v1"
    DENSE_EMBEDDING_API_KEY: Optional[str] = Field(None, description="API key for the dense embedding service (TEI, vLLM, or any other backend)")

    # Sparse (lexical) embedding. Off by default: it needs a second model
    # server, and every vector store today is served by dense retrieval alone.
    # When enabled it is initialized at startup exactly like the dense one, so
    # an unreachable server fails the boot instead of the first ingestion.
    SPARSE_EMBEDDING_ENABLED: bool = False
    SPARSE_EMBEDDING_PROVIDER: str = "vllm"
    SPARSE_EMBEDDING_URL: str = "http://172.17.0.1:8101"
    SPARSE_EMBEDDING_API_KEY: Optional[str] = Field(None, description="API key for the sparse embedding service")

    # Chunking provider selection: "chonkie" or "langchain"
    CHUNKING_PROVIDER: str = "chonkie"
    
    # Postgres Config
    POSTGRES_USER: str = Field(..., description="PostgreSQL username")
    POSTGRES_PASSWORD: str = Field(..., description="PostgreSQL password")
    POSTGRES_DB: str = Field(..., description="PostgreSQL database name")
    POSTGRES_HOST: str = Field(..., description="PostgreSQL host")
    POSTGRES_PORT: int = 5432
    
    # MinIO Config
    MINIO_ROOT_USER: str = Field(..., description="MinIO root user")
    MINIO_ROOT_PASSWORD: str = Field(..., description="MinIO root password")
    MINIO_ENDPOINT_URL: str = Field(..., description="MinIO endpoint URL")
    MINIO_API_PORT: int = 9000
    MINIO_CONSOLE_PORT: int = 9001

    # Backend new vector stores are created on: "qdrant" or "milvus". Every
    # other backend whose connection settings are filled in is connected too,
    # so stores already held there stay searchable.
    VECTOR_STORE_PROVIDER: str = "qdrant"

    # A backend is connected when its block below is filled in; only the block
    # of VECTOR_STORE_PROVIDER is required - see validate_vector_store_credentials.
    # Qdrant Config
    QDRANT_URL: Optional[str] = Field(None, description="Qdrant server URL; fill in to connect qdrant")
    QDRANT_API_KEY: Optional[str] = Field(None, description="Qdrant API key; fill in to connect qdrant")

    # Milvus Config
    MILVUS_URI: Optional[str] = Field(None, description="Milvus server URI; fill in to connect milvus")
    MILVUS_TOKEN: Optional[str] = Field(None, description="Milvus auth token; leave empty on a server without authentication")

    # Langfuse Config (tracing, exported via OpenTelemetry OTLP)
    LANGFUSE_PUBLIC_KEY: str = Field(..., description="Langfuse public key")
    LANGFUSE_SECRET_KEY: str = Field(..., description="Langfuse secret key")
    LANGFUSE_BASE_URL: str = Field(..., description="Self-hosted Langfuse base URL")

    # Redis Config
    REDIS_PORT: int = 6379
    
    # FastAPI Config
    FASTAPI_PORT: int = 8005
    
    @field_validator('API_VERSION')
    @classmethod
    def validate_api_version(cls, v: str) -> str:
        """Validate API version format."""
        if not v.startswith('v'):
            raise ValueError(f'API version must start with "v", got: {v}')
        return v

    @field_validator('NUM_WORKERS', 'POSTGRES_PORT', 'MINIO_API_PORT', 'MINIO_CONSOLE_PORT',
                    'REDIS_PORT', 'FASTAPI_PORT')
    @classmethod
    def validate_positive_ports(cls, v: int) -> int:
        """Validate that port numbers are positive."""
        if v <= 0:
            raise ValueError(f'Port must be positive, got: {v}')
        return v

    # QDRANT_API_KEY is absent on purpose: required only when Qdrant is selected.
    @field_validator('FASTAPI_API_KEY', 'POSTGRES_PASSWORD',
                    'MINIO_ROOT_PASSWORD', 'LANGFUSE_SECRET_KEY')
    @classmethod
    def validate_required_secrets(cls, v: str) -> str:
        """Validate that required secrets are not empty."""
        if not v or v.strip() == "":
            raise ValueError('Required secret cannot be empty')
        return v

    @field_validator('LOG_LEVEL')
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate that log level is one supported by loguru."""
        allowed = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
        normalized = v.upper()
        if normalized not in allowed:
            raise ValueError(f'LOG_LEVEL must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('EMBEDDING_PROVIDER')
    @classmethod
    def validate_embedding_provider(cls, v: str) -> str:
        """Validate that the embedding provider is one this app knows how to build."""
        allowed = {"openai", "tei"}
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'EMBEDDING_PROVIDER must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('SPARSE_EMBEDDING_PROVIDER')
    @classmethod
    def validate_sparse_embedding_provider(cls, v: str) -> str:
        """Validate that the sparse embedding provider is one this app knows how to build."""
        allowed = {"vllm"}
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'SPARSE_EMBEDDING_PROVIDER must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('CHUNKING_PROVIDER')
    @classmethod
    def validate_chunking_provider(cls, v: str) -> str:
        """Validate that the chunking provider is one this app knows how to build."""
        allowed = {"chonkie", "langchain"}
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'CHUNKING_PROVIDER must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('PDF_PARSER_PROVIDER')
    @classmethod
    def validate_pdf_parser_provider(cls, v: str) -> str:
        """Validate that the PDF parser provider is one this app knows how to build."""
        allowed = {"llamaparse"}
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'PDF_PARSER_PROVIDER must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('VECTOR_STORE_PROVIDER')
    @classmethod
    def validate_vector_store_provider(cls, v: str) -> str:
        """Validate that the vector store provider is one this app knows how to build."""
        allowed = set(_VECTOR_STORE_REQUIRED_SETTINGS)
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'VECTOR_STORE_PROVIDER must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @field_validator('LOG_FORMAT')
    @classmethod
    def validate_log_format(cls, v: str) -> str:
        """Validate that log format is a supported mode."""
        allowed = {"auto", "console", "json"}
        normalized = v.lower()
        if normalized not in allowed:
            raise ValueError(f'LOG_FORMAT must be one of {sorted(allowed)}, got: {v}')
        return normalized

    @property
    def enabled_vector_store_providers(self) -> tuple[str, ...]:
        """Backends startup connects, default first.

        A backend is connected when every setting it cannot connect without is
        filled in - a separate list would only be a second place to forget.
        VECTOR_STORE_PROVIDER is always included: it is where new stores are
        created, so it cannot be left disconnected.

        Returns:
            tuple[str, ...]: Provider names, without duplicates
        """
        configured = (name for name, required in _VECTOR_STORE_REQUIRED_SETTINGS.items()
                      if all((getattr(self, setting) or "").strip() for setting in required))
        return (self.VECTOR_STORE_PROVIDER,
                *(name for name in configured if name != self.VECTOR_STORE_PROVIDER))

    # Depends on another field, so it cannot be a field_validator. A Milvus
    # deployment must not have to invent Qdrant credentials, and vice versa.
    @model_validator(mode='after')
    def validate_vector_store_credentials(self) -> 'Settings':
        """Require the connection settings of the backend new stores are created on.

        Every other backend is connected precisely when its settings are
        present, so there is nothing left to require of it here.

        Raises:
            ValueError: If VECTOR_STORE_PROVIDER is missing a setting it cannot
                connect without
        """
        required = _VECTOR_STORE_REQUIRED_SETTINGS[self.VECTOR_STORE_PROVIDER]
        missing = [name for name in required if not (getattr(self, name) or "").strip()]
        if missing:
            raise ValueError(f'Vector store provider "{self.VECTOR_STORE_PROVIDER}" requires '
                             f'{", ".join(missing)} to be set')
        return self


# Global settings instance
settings = Settings()
