from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional


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
    NUM_WORKERS: int = 1
    SERVING_API_KEY: str = Field(..., description="Serving API key for authentication")
    FASTAPI_API_KEY: str = Field(..., description="FastAPI API key for authentication")

    # Logging Config
    LOG_LEVEL: str = Field("INFO", description="Minimum log level captured by SystemLogger")
    LOG_FORMAT: str = Field("auto", description="Log output format: 'auto' (detect TTY), 'console' (colorized text), or 'json' (structured)")
    
    # External API Keys
    UNDATASIO_API_KEY: Optional[str] = Field(None, description="Undatasio API key")
    LLAMAPARSE_API_KEY: Optional[str] = Field(None, description="LlamaParse API key")
    UNSTRUCTURED_API_KEY: Optional[str] = Field(None, description="Unstructured API key")
    UNSTRUCTURED_API_URL: str = "https://api.unstructuredapp.io/general/v0/general"

    # Parsing provider selection for PDFs. Every other format (.txt, .md,
    # .docx, .doc, images) is parsed via the Unstructured API, so only PDF
    # has a backend worth choosing.
    PDF_PARSER_PROVIDER: str = "llamaparse"
    
    # Model Config
    DENSE_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    VLLM_DENSE_EMBEDDING_URL: str = "http://172.17.0.1:8100/v1"

    # Embedding provider selection: "openai" (OpenAI-compatible endpoint, e.g. vLLM)
    # or "tei" (raw HTTP request to a Text Embeddings Inference /embed endpoint)
    EMBEDDING_PROVIDER: str = "openai"
    TEI_EMBEDDING_URL: str = "http://localhost:8100"
    TEI_API_KEY: Optional[str] = Field(None, description="API key for the TEI embedding service")

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

    # Vector store provider selection: "qdrant" or "milvus" (milvus not implemented yet).
    # Only affects newly created vector stores - existing ones are read back with
    # the provider recorded on their database row.
    VECTOR_STORE_PROVIDER: str = "qdrant"

    # Qdrant Config
    QDRANT_URL: str = Field(..., description="Qdrant server URL")
    QDRANT_API_KEY: str = Field(..., description="Qdrant API key")
    QDRANT_PORT: int = 6333

    # Milvus Config (unused until the Milvus backend is implemented)
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_TOKEN: Optional[str] = Field(None, description="Milvus auth token")

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
                    'QDRANT_PORT', 'REDIS_PORT', 'FASTAPI_PORT')
    @classmethod
    def validate_positive_ports(cls, v: int) -> int:
        """Validate that port numbers are positive."""
        if v <= 0:
            raise ValueError(f'Port must be positive, got: {v}')
        return v

    @field_validator('SERVING_API_KEY', 'FASTAPI_API_KEY', 'POSTGRES_PASSWORD',
                    'MINIO_ROOT_PASSWORD', 'QDRANT_API_KEY', 'LANGFUSE_SECRET_KEY')
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
        allowed = {"qdrant", "milvus"}
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


# Global settings instance
settings = Settings()
