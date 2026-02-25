# Langchain component
from langchain_openai import OpenAIEmbeddings
# DB
from app.db.postgres import PostgresClient
from app.db.minio import MinioService
from app.db.qdrant import QdrantService
# Postgres
from asyncpg import PostgresError
# Config
from app.core.config import *
# Other component
import requests, time, asyncio, mlflow, re, os, asyncpg
# Logger
from loggers import SystemLogger

# Set MLflow params
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
# Correct MLFlow and Minio for logging messages
os.environ["AWS_ACCESS_KEY_ID"] = MINIO_ROOT_USER
os.environ["AWS_SECRET_ACCESS_KEY"] = MINIO_ROOT_PASSWORD
os.environ["MLFLOW_S3_ENDPOINT_URL"] = MLFLOW_S3_ENDPOINT_URL
os.environ["MLFLOW_DEFAULT_ARTIFACT_ROOT"] = MLFLOW_DEFAULT_ARTIFACT_ROOT

async def init_embed_model(serving_service_name :str = "vllm",
                           port :int = 8000) -> OpenAIEmbeddings:
    """
    Initialize and test the embedding model service.

    Creates an OpenAIEmbeddings instance configured for the specified
    serving service and tests connectivity with a sample embedding.

    Args:
        serving_service_name (str): Name of the embedding service (default: "vllm")
        port (int): Port number for the embedding service (default: 8000)

    Returns:
        OpenAIEmbeddings: Configured embedding model instance

    Raises:
        Exception: If embedding service is not reachable or fails to respond
    """
    global embed_model
    embed_model = OpenAIEmbeddings(model = EMBEDDING_MODEL_NAME,
                                   base_url = f"http://{serving_service_name}:{port}/v1",
                                   api_key = SERVING_API_KEY)

    try:
        resp = await embed_model.aembed_documents(["Hello"])
        SystemLogger.warning(f"[STARTUP] {serving_service_name.capitalize()} embedding service ready")
    except:
        # Response
        SystemLogger.error("[STARTUP] Init {serving_service_name.capitalize()} embedding service failed")
    return embed_model

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

    Creates a MinioService instance and ensures required buckets exist:
    - MLflow bucket (extracted from MLFLOW_DEFAULT_ARTIFACT_ROOT)
    - Uploaded file bucket (from UPLOADED_FILE_BUCKET config)

    Returns:
        MinioService: Configured MinIO service instance

    Raises:
        ValueError: If MLFLOW_DEFAULT_ARTIFACT_ROOT format is invalid
        Exception: If bucket creation fails (except when bucket already exists)
    """
    global minio_service
    # Define url
    minio_endpoint_url = MLFLOW_S3_ENDPOINT_URL.replace("http://","")
    # Get bucket name
    match = re.search(r'^s3://([^/]+)/', MLFLOW_DEFAULT_ARTIFACT_ROOT)

    # Raise exception when not found
    if not match:
        raise ValueError(f"MLFLOW_DEFAULT_ARTIFACT_ROOT with incorrect format: {MLFLOW_DEFAULT_ARTIFACT_ROOT}")
    bucket_name = match.group(1)

    # Init connection
    minio_service = MinioService(endpoint_url = minio_endpoint_url,
                                 access_key = MINIO_ROOT_USER,
                                 secret_key = MINIO_ROOT_PASSWORD)

    # Create bucket for mlflow
    if not minio_service.client.bucket_exists(bucket_name):
        try:
            minio_service.client.make_bucket(bucket_name)
            SystemLogger.success(f"Create Minio bucker for MLflow ({bucket_name}) done!")
        except Exception as e:
            if "BucketAlreadyOwnedByYou" in str(e):
                SystemLogger.info(f"Minio bucket for MLflow ({bucket_name}) already exists")
            else:
                raise e

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

def init_qdrant() -> QdrantService:
    """
    Initialize Qdrant vector database service.

    Creates and configures a QdrantService instance for vector storage
    and retrieval operations.

    Returns:
        QdrantService: Configured Qdrant service instance
    """
    global qdrant_service
    # Init connection
    qdrant_service = QdrantService(url = QDRANT_URL)
    return qdrant_service

def get_embed_model() -> OpenAIEmbeddings:
    """
    Get the global embedding model instance.

    Returns:
        OpenAIEmbeddings: The initialized embedding model

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

def wait_for_serving(serving_service_name :str = "vllm",
                     serving_port :int = 8000,
                     wait_time :int = 5,
                     max_wait: int = 120) -> bool:
    """
    Wait for a serving service to become healthy and reachable.

    Continuously polls the service's health endpoint until it responds
    with status 200 or the maximum wait time is exceeded.

    Args:
        serving_service_name (str): Name of the service to wait for (default: "vllm")
        serving_port (int): Port number of the service (default: 8000)
        wait_time (int): Time to wait between attempts in seconds (default: 5)
        max_wait (int): Maximum total wait time in seconds (default: 120)

    Returns:
        bool: True if service becomes reachable, False if timeout is exceeded
    """
    # Define url
    serving_url = f"http://{serving_service_name}:{serving_port}"
    start_time = time.time()

    # Loop
    while True:
        try:
            response = requests.get(f"{serving_url}/health", timeout=1)
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass

        # Check total wait time
        elapsed = time.time() - start_time
        if elapsed >= max_wait:
            SystemLogger.error(f"❌ Timeout: {serving_service_name} not reachable after {max_wait}s. Try increasing max_wait params or changing config service")
            return False

        time.sleep(wait_time)

async def wait_for_postgres(create_pool_func,
                            retries: int = 5,
                            delay: float = 0.5) -> None:
    """
    Wait for PostgreSQL connection to be established with retry logic.

    Attempts to create a PostgreSQL connection pool with configurable
    retry logic and exponential backoff.

    Args:
        create_pool_func: Async function to create the connection pool
        retries (int): Maximum number of connection attempts (default: 5)
        delay (float): Delay between retries in seconds (default: 0.5)

    Raises:
        ConnectionRefusedError: If unable to connect after all retries
        PostgresError: If PostgreSQL-specific errors occur
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            await create_pool_func
            SystemLogger.warning(f"[STARTUP] PostgreSQL connection established (attempt {attempt}/{retries})")
            return
        except (ConnectionRefusedError, PostgresError) as e:
            last_exc = e
            SystemLogger.error(f"[STARTUP] PostgreSQL connection failed (attempt {attempt}/{retries}): {e!r}")
            if attempt < retries:
                await asyncio.sleep(delay)