# OpenAI client
from openai import AsyncOpenAI
# DB
from app.db.postgres import PostgresClient
from app.db.minio import MinioService
from app.db.qdrant import QdrantService
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

async def init_embed_model() -> AsyncOpenAI:
    """
    Initialize and test the dense embedding model service.

    Creates an AsyncOpenAI client configured for the vLLM dense embedding
    service and tests connectivity with a sample embedding.

    Returns:
        AsyncOpenAI: Configured embedding client instance

    Raises:
        Exception: If embedding service is not reachable or fails to respond
    """
    global embed_model
    embed_model = AsyncOpenAI(base_url = VLLM_DENSE_EMBEDDING_URL,
                              api_key = SERVING_API_KEY)

    try:
        await embed_model.embeddings.create(model = DENSE_MODEL_NAME, input = ["Hello"])
        SystemLogger.success("[STARTUP] Dense embedding service ready")
    except Exception as e:
        SystemLogger.error(f"[STARTUP] Init dense embedding service failed: {e!r}")
        raise
    return embed_model

async def get_dense_embedding(texts: List[str]) -> List[List[float]]:
    """
    Generate dense embedding vectors for a list of texts via the vLLM service.

    Args:
        texts (List[str]): Texts to embed

    Returns:
        List[List[float]]: Embedding vector for each input text
    """
    response = await embed_model.embeddings.create(model = DENSE_MODEL_NAME, input = texts)
    return [item.embedding for item in response.data]

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

async def init_qdrant() -> QdrantService:
    """
    Initialize Qdrant vector database service.

    Creates and configures a QdrantService instance for vector storage
    and retrieval operations, and verifies connectivity.

    Returns:
        QdrantService: Configured Qdrant service instance

    Raises:
        Exception: If Qdrant is not reachable
    """
    global qdrant_service
    # Init connection
    qdrant_service = QdrantService(url = QDRANT_URL, api_key = QDRANT_API_KEY)
    try:
        await qdrant_service._check_connection()
        SystemLogger.success("[STARTUP] Qdrant connection established")
    except Exception as e:
        SystemLogger.error(f"[STARTUP] Qdrant connection failed: {e!r}")
        raise
    return qdrant_service

def get_embed_model() -> AsyncOpenAI:
    """
    Get the global embedding model instance.

    Returns:
        AsyncOpenAI: The initialized embedding client

    Raises:
        AttributeError: If embedding model has not been initialized
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

def get_minio_service() -> MinioService:
    """
    Get the global MinIO service instance.

    Returns:
        MinioService: The initialized MinIO service

    Raises:
        AttributeError: If MinIO service has not been initialized
    """
    return minio_service

def get_qdrant_service() -> QdrantService:
    """
    Get the global Qdrant service instance.

    Returns:
        QdrantService: The initialized Qdrant service

    Raises:
        AttributeError: If Qdrant service has not been initialized
    """
    return qdrant_service

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