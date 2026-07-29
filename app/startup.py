# Embedding
from app.components.embedding import EmbeddingService
# Parsing
from app.components.parsing import ParsingService
# DB
from app.db.postgres import PostgresClient
from app.db.minio import MinioService
from app.db.vector_store import BaseVectorStoreConnection, VectorStoreFactory
# Postgres
from asyncpg import PostgresError
# Config
from app.core.config import *
# Tracing
from app.core.tracing import init_tracing
# Other component
import requests, time, asyncio, asyncpg
from typing import List
# Logger
from loggers import SystemLogger

async def init_embed_model() -> EmbeddingService:
    """
    Initialize and test the dense embedding service.

    Builds the EmbeddingService for the provider selected via
    EMBEDDING_PROVIDER (OpenAI-compatible endpoint or raw TEI /embed
    request) and tests connectivity with a sample embedding.

    Returns:
        EmbeddingService: Configured embedding service instance

    Raises:
        Exception: If embedding service is not reachable or fails to respond
    """
    global embed_model
    embed_model = EmbeddingService.from_settings()

    try:
        await embed_model.check_connection()
        SystemLogger.success("[STARTUP] Dense embedding service ready")
    except Exception as e:
        SystemLogger.error(f"[STARTUP] Init dense embedding service failed: {e!r}")
        raise
    return embed_model

async def get_dense_embedding(texts: List[str]) -> List[List[float]]:
    """
    Generate dense embedding vectors for a list of texts via the configured embedding service.

    Args:
        texts (List[str]): Texts to embed

    Returns:
        List[List[float]]: Embedding vector for each input text
    """
    return await embed_model.embed(texts)

async def get_dense_embedding_dim() -> int:
    """
    Return the cached embedding dimension, set once during init_embed_model().

    Returns:
        int: Dimension of vectors produced by the configured embedding model

    Raises:
        RuntimeError: If called before init_embed_model() has run
    """
    if embed_model.dimension is None:
        raise RuntimeError("Embedding service not initialized yet")
    return embed_model.dimension

def init_parsing_service() -> ParsingService:
    """
    Initialize the document parsing service.

    Builds the providers selected via config (PDF_PARSER_PROVIDER for PDFs,
    Unstructured for everything else). Constructed once so each backend's
    client is reused across files rather than rebuilt per ingestion.

    Returns:
        ParsingService: Configured parsing service instance

    Raises:
        ValueError: If a selected provider is misconfigured (e.g. missing API key)
    """
    global parsing_service
    parsing_service = ParsingService.from_settings()
    SystemLogger.success(f"[STARTUP] Parsing service ready "
                         f"({', '.join(parsing_service.supported_extensions)})")
    return parsing_service


def get_parsing_service() -> ParsingService:
    """
    Get the global parsing service instance.

    Returns:
        ParsingService: The initialized parsing service

    Raises:
        NameError: If the parsing service has not been initialized
    """
    return parsing_service


def init_postgres() -> PostgresClient:
    """
    Initialize PostgreSQL database client.

    Creates and configures a PostgresClient instance with connection
    parameters from environment configuration. The client will need
    to have its connection pool created separately via _create_pool().

    Returns:
        PostgresClient: Configured PostgreSQL client instance
    """
    global postgres_client
    # Init connection
    postgres_client = PostgresClient(user = POSTGRES_USER,
                                     password = POSTGRES_PASSWORD,
                                     database = POSTGRES_DB,
                                     host = POSTGRES_HOST,
                                     port = 5432)
    return postgres_client

def init_minio() -> MinioService:
    """
    Initialize MinIO object storage service.

    Creates a MinioService instance and ensures the uploaded file bucket
    (from UPLOADED_FILE_BUCKET config) exists.

    Returns:
        MinioService: Configured MinIO service instance

    Raises:
        Exception: If bucket creation fails (except when bucket already exists)
    """
    global minio_service
    # Init connection
    minio_service = MinioService(endpoint_url = MINIO_ENDPOINT_URL.replace("http://",""),
                                 access_key = MINIO_ROOT_USER,
                                 secret_key = MINIO_ROOT_PASSWORD)

    # Create bucket for Uploaded File
    if not minio_service.client.bucket_exists(UPLOADED_FILE_BUCKET):
        try:
            minio_service.client.make_bucket(UPLOADED_FILE_BUCKET)
            SystemLogger.success(f"Create Minio bucker for Uploaded File ({UPLOADED_FILE_BUCKET}) done!")
        except Exception as e:
            if "BucketAlreadyOwnedByYou" in str(e):
                SystemLogger.info(f"Minio bucket for Uploaded File ({UPLOADED_FILE_BUCKET}) already exists")
            else:
                raise e
    return minio_service

async def init_vector_store() -> BaseVectorStoreConnection:
    """
    Initialize the configured vector database connection.

    Builds the connection for the provider selected via VECTOR_STORE_PROVIDER,
    verifies connectivity, and registers it with VectorStoreFactory so call
    sites can obtain per-collection stores without knowing the backend.

    Returns:
        BaseVectorStoreConnection: Live vector store connection

    Raises:
        Exception: If the vector database is not reachable
    """
    global vector_store_connection
    provider = VectorStoreFactory.default_provider()
    # Init connection
    vector_store_connection = VectorStoreFactory.connection_class(provider).from_settings()
    try:
        await vector_store_connection.check_connection()
        SystemLogger.success(f"[STARTUP] Vector store connection established ({provider})")
    except Exception as e:
        SystemLogger.error(f"[STARTUP] Vector store connection failed ({provider}): {e!r}")
        raise
    # Make it reachable through the factory
    VectorStoreFactory.register_connection(provider, vector_store_connection)
    return vector_store_connection

def get_embed_model() -> EmbeddingService:
    """
    Get the global embedding service instance.

    Returns:
        EmbeddingService: The initialized embedding service

    Raises:
        AttributeError: If embedding service has not been initialized
    """
    return embed_model

def get_postgres_pool() -> asyncpg.Pool:
    """
    Get the PostgreSQL connection pool.

    Returns:
        asyncpg.Pool: The PostgreSQL connection pool

    Raises:
        AttributeError: If postgres client or pool has not been initialized
    """
    return postgres_client.pool

def get_postgres_client() -> PostgresClient:
    """
    Get the global PostgreSQL client instance.

    Returns:
        PostgresClient: The initialized PostgreSQL client, e.g. for graceful
            shutdown via its close() method

    Raises:
        NameError: If the PostgreSQL client has not been initialized
    """
    return postgres_client

def get_minio_service() -> MinioService:
    """
    Get the global MinIO service instance.

    Returns:
        MinioService: The initialized MinIO service

    Raises:
        AttributeError: If MinIO service has not been initialized
    """
    return minio_service

def get_vector_store_connection() -> BaseVectorStoreConnection:
    """
    Get the global vector store connection instance.

    Returns:
        BaseVectorStoreConnection: The initialized vector store connection

    Raises:
        NameError: If the vector store connection has not been initialized
    """
    return vector_store_connection

async def wait_for_postgres(pool: asyncpg.Pool,
                            retries: int = 5,
                            delay: float = 0.5) -> None:
    """
    Wait for PostgreSQL connection to be established with retry logic.

    Attempts to create a PostgreSQL connection pool with configurable
    retry logic and exponential backoff.

    Args:
        pool: The PostgreSQL connection pool to wait on
        retries (int): Maximum number of connection attempts (default: 5)
        delay (float): Delay between retries in seconds (default: 0.5)

    Raises:
        ConnectionRefusedError: If unable to connect after all retries
        PostgresError: If PostgreSQL-specific errors occur
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            await pool
            SystemLogger.success(f"[STARTUP] PostgreSQL connection established (attempt {attempt}/{retries})")
            return
        except (ConnectionRefusedError, PostgresError) as e:
            last_exc = e
            SystemLogger.error(f"[STARTUP] PostgreSQL connection failed (attempt {attempt}/{retries}): {e!r}")
            if attempt < retries:
                await asyncio.sleep(delay)
    # Exhausted every retry - surface the last failure instead of silently
    # continuing as if Postgres were reachable.
    SystemLogger.error(f"[STARTUP] PostgreSQL unreachable after {retries} attempts")
    raise last_exc