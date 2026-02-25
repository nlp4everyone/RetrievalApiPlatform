from fastapi import FastAPI
# Define startup
from .startup import *
# Router
from .router import (embedding_router,
                     file_router,
                     vector_store_router)
# Exception
from .exceptions import BaseException
from .exceptions.handlers import common_exception_handler
# Config
from .core.config import *

# Components
import time, logging
# Logger
from loggers import SystemLogger
logging.getLogger("uvicorn.error").propagate = False


# Define OpenAPI tags for API documentation
tags_metadata = [
    {
        "name": "Embedding",
        "description": "Contains operations related to embedding vectors",
    },
    {
        "name": "File",
        "description": "Provides a secure way to upload, manage, and retrieve files",
    },
    {
        "name": "Vector Stores",
        "description": "Provides a secure way to create, manage, and retrieve vector store",
    },
]
# Initialize FastAPI application with OpenAPI tags
app = FastAPI(openapi_tags = tags_metadata)

# Register API Routes
# Add embedding router for vector embedding operations
app.include_router(embedding_router,
                   prefix = "/v1",
                   tags = [tags_metadata[0].get("name")])
# Add file router for file upload and management operations
app.include_router(file_router,
                   prefix = "/v1",
                   tags = [tags_metadata[1].get("name")])
# Add vector store router for vector database operations
app.include_router(vector_store_router,
                   prefix = "/v1",
                   tags = [tags_metadata[2].get("name")])

# Register global exception handler for custom exceptions
app.add_exception_handler(BaseException, common_exception_handler)

# Application Startup Event
# Initializes all required services and dependencies
@app.on_event("startup")
async def startup_event():
    SystemLogger.info("[APP] Starting application warm up...")
    # Start
    start = time.perf_counter()

    # Wait until embedding model started
    wait_for_serving(serving_service_name = EMBEDDING_SERVICE_NAME,
                     serving_port = DOCKER_EMBEDDING_PORT,
                     max_wait = 60)

    # Init embed model
    await init_embed_model(serving_service_name = EMBEDDING_SERVICE_NAME,
                           port = DOCKER_EMBEDDING_PORT)
    SystemLogger.info("[APP] ✅ Serving embedding ready!")


    # Init Postgres
    postgres_client = init_postgres()
    # Create pool and wait for postgres
    await postgres_client._create_pool()
    # Wait for postgres
    await wait_for_postgres(postgres_client.pool)
    # Create table if not existed
    await postgres_client._create_table()
    SystemLogger.info("[APP] ✅ Postgres ready")
    # Init Minio
    init_minio()
    SystemLogger.info("[APP] ✅ MinIO ready")
    # Logging
    SystemLogger.info(" ✅ Minio ready!")
    SystemLogger.success(f"[APP] Service started in {round(time.perf_counter() - start,1)}s")