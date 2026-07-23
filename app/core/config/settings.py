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
    
    # Model Config
    DENSE_MODEL_NAME: str = "Qwen/Qwen3-Embedding-0.6B"
    VLLM_DENSE_EMBEDDING_URL: str = "http://172.17.0.1:8100/v1"
    
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

    # Qdrant Config
    QDRANT_URL: str = Field(..., description="Qdrant server URL")
    QDRANT_API_KEY: str = Field(..., description="Qdrant API key")
    QDRANT_PORT: int = 6333

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
