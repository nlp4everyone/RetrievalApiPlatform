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
    MINIO_API_PORT: int = 9000
    MINIO_CONSOLE_PORT: int = 9001
    
    # Qdrant Config
    QDRANT_URL: str = Field(..., description="Qdrant server URL")
    QDRANT_API_KEY: str = Field(..., description="Qdrant API key")
    QDRANT_PORT: int = 6333
    
    # MLFlow Config
    MLFLOW_TRACKING_URI: str = Field(..., description="MLflow tracking URI")
    MLFLOW_EXPERIMENT_NAME: str = "Experiment"
    MLFLOW_DEFAULT_ARTIFACT_ROOT: str = "s3://mlflow/artifacts"
    MLFLOW_S3_ENDPOINT_URL: str = Field(..., description="MLflow S3 endpoint URL")
    MLFLOW_PORT: int = 5000
    
    # Redis Config
    REDIS_PORT: int = 6379
    
    # FastAPI Config
    FASTAPI_PORT: int = 8005
    
    @field_validator('API_VERSION')
    @classmethod
    def validate_api_version(cls, v):
        """Validate API version format."""
        if not v.startswith('v'):
            raise ValueError(f'API version must start with "v", got: {v}')
        return v
    
    @field_validator('NUM_WORKERS', 'POSTGRES_PORT', 'MINIO_API_PORT', 'MINIO_CONSOLE_PORT', 
                    'QDRANT_PORT', 'MLFLOW_PORT', 'REDIS_PORT', 'FASTAPI_PORT')
    @classmethod
    def validate_positive_ports(cls, v):
        """Validate that port numbers are positive."""
        if v <= 0:
            raise ValueError(f'Port must be positive, got: {v}')
        return v
    
    @field_validator('SERVING_API_KEY', 'FASTAPI_API_KEY', 'POSTGRES_PASSWORD', 
                    'MINIO_ROOT_PASSWORD', 'QDRANT_API_KEY')
    @classmethod
    def validate_required_secrets(cls, v):
        """Validate that required secrets are not empty."""
        if not v or v.strip() == "":
            raise ValueError('Required secret cannot be empty')
        return v


# Global settings instance
settings = Settings()
