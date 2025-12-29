# Langchain component
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
# DB
from app.db.postgres import PostgresService
from app.db.minio import MinioService
from app.db.qdrant import QdrantService
# Postgres
from asyncpg import PostgresError
# Config
from app.core.config.constants import *
from app.core.config.service_params import *
# Other component
import requests, time, asyncio, mlflow, re
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

async def init_model(serving_service_name :str = "vllm",
                     port :int = 8000):
    """Start Postgres Connection"""
    global llm
    llm = ChatOpenAI(model = MODEL_NAME,
                     base_url = f"http://{serving_service_name}:{port}/v1",
                     streaming = True,
                     api_key = SERVING_API_KEY,
                     extra_body={
                         "chat_template_kwargs": {"enable_thinking": False}
                     })

    try:
        resp = await llm.ainvoke("Hello")
    except:
        # Response
        SystemLogger.error("❌ Failed to get response from LLM!")
    return llm

async def init_embed_model(serving_service_name :str = "vllm",
                           port :int = 8000) -> OpenAIEmbeddings:
    """Start Postgres Connection"""
    global embed_model
    embed_model = OpenAIEmbeddings(model = MODEL_NAME,
                                   base_url = f"http://{serving_service_name}:{port}/v1",
                                   api_key = SERVING_API_KEY)

    try:
        resp = await embed_model.aembed_documents(["Hello"])
    except:
        # Response
        SystemLogger.error("❌ Failed to get response from embedding model!")
    return embed_model

def init_postgres():
    """Start Postgres Connection"""
    global postgres_service
    # Init connection
    postgres_service = PostgresService(user = POSTGRES_USER,
                                       password = POSTGRES_PASSWORD,
                                       database = POSTGRES_DB,
                                       host = POSTGRES_HOST,
                                       port = 5432)
    return postgres_service

def init_minio():
    """Start Postgres Connection"""
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
        minio_service.client.make_bucket(bucket_name)
        SystemLogger.success(f"Create Minio bucker for MLflow ({bucket_name}) done!")

    # Create bucket for Uploaded File
    if not minio_service.client.bucket_exists(UPLOADED_FILE_BUCKET):
        minio_service.client.make_bucket(UPLOADED_FILE_BUCKET)
        SystemLogger.success(f"Create Minio bucker for Uploaded File ({UPLOADED_FILE_BUCKET}) done!")
    return minio_service

def init_qdrant():
    """Start Postgres Connection"""
    global qdrant_service
    # Init connection
    qdrant_service = QdrantService(url = "http://qdrant:6333")
    return qdrant_service

def get_model():
    return llm

def get_embed_model():
    return embed_model

def get_postgres_pool():
    return postgres_service.pool

def get_minio_service():
    return minio_service

def get_qdrant_service():
    return qdrant_service

def wait_for_serving(serving_service_name :str = "vllm",
                     serving_port :int = 8000,
                     wait_time :int = 5,
                     max_wait: int = 120):
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
                            delay: float = 0.5):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            await create_pool_func
            SystemLogger.info(f"✅ Postgres ready (on attempt {attempt})")
            return
        except (ConnectionRefusedError, PostgresError) as e:
            last_exc = e
            SystemLogger.error(f"❌ Postgres not ready (attempt {attempt}/{retries}): {e!r}")
            if attempt < retries:
                await asyncio.sleep(delay)