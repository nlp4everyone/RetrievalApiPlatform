# Embedding
from app.components.embedding import EmbeddingService, SparseEmbeddingService
from app.components.embedding.base import SparseVector
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
# Other component
import asyncio, asyncpg
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional
# Logger
from loggers import SystemLogger

# Set by init_sparse_embed_model(); stays None when SPARSE_EMBEDDING_ENABLED is
# false, so callers can tell "sparse is switched off" from "startup never ran"
sparse_embed_model: Optional[SparseEmbeddingService] = None

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

async def close_embed_model() -> None:
    """Close the dense embedding service's connections (called on shutdown)."""
    await embed_model.aclose()

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

async def init_sparse_embed_model() -> Optional[SparseEmbeddingService]:
    """
    Initialize and test the sparse embedding service, if it is switched on.

    Mirrors init_embed_model(): builds the service for the provider selected
    via SPARSE_EMBEDDING_PROVIDER and probes it, so an unreachable server fails
    the boot rather than the first ingestion. Unlike the dense one this is
    opt-in - with SPARSE_EMBEDDING_ENABLED false the service is left unset and
    startup carries on dense-only.

    Returns:
        Optional[SparseEmbeddingService]: Configured sparse embedding service,
            or None when SPARSE_EMBEDDING_ENABLED is false

    Raises:
        Exception: If sparse embedding is enabled but the service is not
            reachable or fails to respond
    """
    global sparse_embed_model
    if not SPARSE_EMBEDDING_ENABLED:
        sparse_embed_model = None
        SystemLogger.info("[STARTUP] Sparse embedding service disabled (SPARSE_EMBEDDING_ENABLED=false)")
        return None

    sparse_embed_model = SparseEmbeddingService.from_settings()

    try:
        await sparse_embed_model.check_connection()
        SystemLogger.success("[STARTUP] Sparse embedding service ready")
    except Exception as e:
        SystemLogger.error(f"[STARTUP] Init sparse embedding service failed: {e!r}")
        raise
    return sparse_embed_model

async def close_sparse_embed_model() -> None:
    """Close the sparse embedding service's connections, if one was built."""
    if sparse_embed_model is not None:
        await sparse_embed_model.aclose()

async def get_sparse_embedding(texts: List[str]) -> List[SparseVector]:
    """
    Generate sparse embeddings for a list of texts via the configured sparse service.

    Args:
        texts (List[str]): Texts to embed

    Returns:
        List[SparseVector]: {token_id: weight} mapping for each input text

    Raises:
        RuntimeError: If sparse embedding is disabled or has not been initialized
    """
    return await get_sparse_embed_model().embed(texts)

def get_sparse_embed_model() -> SparseEmbeddingService:
    """
    Get the global sparse embedding service instance.

    Returns:
        SparseEmbeddingService: The initialized sparse embedding service

    Raises:
        RuntimeError: If sparse embedding is disabled (SPARSE_EMBEDDING_ENABLED
            is false) or init_sparse_embed_model() has not run yet
    """
    if sparse_embed_model is None:
        raise RuntimeError("Sparse embedding service not available - set "
                           "SPARSE_EMBEDDING_ENABLED=true and make sure "
                           "init_sparse_embed_model() has run")
    return sparse_embed_model

def is_sparse_embedding_enabled() -> bool:
    """
    Whether a sparse embedding service is live in this process.

    Lets callers branch on availability without catching the RuntimeError that
    get_sparse_embed_model() raises when sparse embedding is switched off.

    Returns:
        bool: True once init_sparse_embed_model() has built a service
    """
    return sparse_embed_model is not None

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
    (UPLOADED_FILE_BUCKET) and the parsed-text bucket (PARSED_TEXT_BUCKET) exist.

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
    _ensure_bucket(minio_service, UPLOADED_FILE_BUCKET, "Uploaded File")
    # And the one holding parsed Markdown, kept separate so retention rules
    # for derived text and for the originals can differ
    _ensure_bucket(minio_service, PARSED_TEXT_BUCKET, "Parsed Text")
    return minio_service


def _ensure_bucket(minio_service: MinioService, bucket: str, label: str) -> None:
    """
    Create a bucket if it is not there yet.

    Args:
        minio_service: Connected MinIO service
        bucket: Bucket name to create
        label: Human-readable name for the log line

    Raises:
        Exception: If bucket creation fails for any reason other than the
            bucket having been created concurrently
    """
    if minio_service.client.bucket_exists(bucket):
        return
    try:
        minio_service.client.make_bucket(bucket)
        SystemLogger.success(f"Create Minio bucket for {label} ({bucket}) done!")
    except Exception as e:
        if "BucketAlreadyOwnedByYou" in str(e):
            SystemLogger.info(f"Minio bucket for {label} ({bucket}) already exists")
        else:
            raise e

async def init_vector_store() -> BaseVectorStoreConnection:
    """
    Initialize every configured vector database connection.

    Connects the default backend and every other one whose settings are filled
    in, verifies each, and registers it with VectorStoreFactory so call sites
    can obtain per-collection stores without knowing the backend. Connecting
    more than one is what lets stores on different engines be searched side by
    side: each record names the backend holding it, while new stores go to
    VECTOR_STORE_PROVIDER.

    The default backend fails the boot when unreachable - new stores are created
    on it, so a service that cannot reach it has nothing to offer. A secondary
    one is skipped with a warning instead: it is connected because its settings
    are present, and a leftover .env block is enough for that.

    Returns:
        BaseVectorStoreConnection: Connection for the default provider

    Raises:
        Exception: If the default vector database is not reachable
    """
    default_provider = VectorStoreFactory.default_provider()
    for provider in VectorStoreFactory.enabled_providers():
        try:
            # Inside the try: this resolves the backend module, so a missing or
            # broken SDK is caught here too, not only an unreachable server
            connection = VectorStoreFactory.connection_class(provider).from_settings()
            await connection.check_connection()
            SystemLogger.success(f"[STARTUP] Vector store connection established ({provider})")
        except Exception as e:
            if provider == default_provider:
                SystemLogger.error(f"[STARTUP] Vector store connection failed ({provider}): {e!r}")
                raise
            # Refusing to boot over a backend nothing may be stored on would
            # make a stale setting fatal; a store that does live there raises
            # at query time instead (see VectorStoreFactory.get_connection).
            SystemLogger.warning(f"[STARTUP] Vector store not connected ({provider}): {e!r} - "
                                 f"vector stores held by it will fail at query time")
            continue
        # Make it reachable through the factory
        VectorStoreFactory.register_connection(provider, connection)

    return VectorStoreFactory.get_connection()

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

def init_io_executor() -> ThreadPoolExecutor:
    """
    Initialize the thread pool used for blocking I/O (MinIO upload/download/delete).

    Kept separate from the CPU thread pool so a slow network transfer can
    never queue behind - or starve out - CPU-bound chunking work, and vice versa.

    Returns:
        ThreadPoolExecutor: The I/O thread pool
    """
    global io_executor
    io_executor = ThreadPoolExecutor(max_workers = IO_THREAD_POOL_SIZE, thread_name_prefix = "io")
    SystemLogger.success(f"[STARTUP] I/O thread pool ready (max_workers={IO_THREAD_POOL_SIZE})")
    return io_executor


def get_io_executor() -> ThreadPoolExecutor:
    """
    Get the global I/O thread pool.

    Returns:
        ThreadPoolExecutor: The initialized I/O thread pool

    Raises:
        NameError: If init_io_executor() has not run yet
    """
    return io_executor


def init_cpu_executor() -> ThreadPoolExecutor:
    """
    Initialize the thread pool used for CPU-bound chunking work.

    Sized to the machine's cores (see CPU_THREAD_POOL_SIZE) rather than the
    I/O pool's size, since oversubscribing CPU-bound work just adds context
    switching, not throughput.

    Returns:
        ThreadPoolExecutor: The CPU thread pool
    """
    global cpu_executor
    cpu_executor = ThreadPoolExecutor(max_workers = CPU_THREAD_POOL_SIZE, thread_name_prefix = "cpu")
    SystemLogger.success(f"[STARTUP] CPU thread pool ready (max_workers={CPU_THREAD_POOL_SIZE})")
    return cpu_executor


def get_cpu_executor() -> ThreadPoolExecutor:
    """
    Get the global CPU thread pool.

    Returns:
        ThreadPoolExecutor: The initialized CPU thread pool

    Raises:
        NameError: If init_cpu_executor() has not run yet
    """
    return cpu_executor


def init_storage_semaphore() -> asyncio.Semaphore:
    """
    Initialize the semaphore capping concurrent MinIO work per worker process.

    Every blocking MinIO call in the ingestion pipeline - the file download,
    the parse-cache lookup, the artifact upload - runs on the I/O thread pool,
    so they share one limit. Capping only some of them would leave the pool
    just as exhaustible by whichever consumer was left uncapped.

    Returns:
        asyncio.Semaphore: The storage concurrency semaphore
    """
    global storage_semaphore
    storage_semaphore = asyncio.Semaphore(STORAGE_CONCURRENCY)
    SystemLogger.success(f"[STARTUP] Storage concurrency limit ready (limit={STORAGE_CONCURRENCY})")
    return storage_semaphore


def get_storage_semaphore() -> asyncio.Semaphore:
    """
    Get the global storage concurrency semaphore.

    Returns:
        asyncio.Semaphore: The initialized storage concurrency semaphore

    Raises:
        NameError: If init_storage_semaphore() has not run yet
    """
    return storage_semaphore


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