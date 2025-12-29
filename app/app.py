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
# DB
from .db.postgres import PostgresFileStore, PostgresVectorStore
# Config
from .core.config.service_params import *

# Components
import time, logging
# Logger
from loggers import SystemLogger
logging.getLogger("uvicorn.error").propagate = False

# Tags
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
# Define app
app = FastAPI(openapi_tags = tags_metadata)
# Add embedding route
app.include_router(embedding_router,
                   prefix = "/v1",
                   tags = [tags_metadata[0].get("name")])
# Add file route
app.include_router(file_router,
                   prefix = "/v1",
                   tags = [tags_metadata[1].get("name")])
# Add file route
app.include_router(vector_store_router,
                   prefix = "/v1",
                   tags = [tags_metadata[2].get("name")])

# Add exception
app.add_exception_handler(BaseException, common_exception_handler)

@app.on_event("startup")
async def startup_event():
    SystemLogger.info(f"Waiting for warm up ...")
    # Start
    start = time.perf_counter()
    # # Wait until llm model started
    # wait_for_serving(serving_service_name = SERVING_SERVICE_NAME,
    #                  serving_port = 30000)
    # SystemLogger.info(" ✅ Serving LLM service ready!")

    # Wait until embedding model started
    wait_for_serving(serving_service_name = EMBEDDING_SERVING_SERVICE_NAME,
                     serving_port = 30001,
                     max_wait = 60)

    # # Init ml model
    # await init_model(serving_service_name=SERVING_SERVICE_NAME,
    #                  port=30000)  # Default port for vLLM

    # Init embed model
    await init_embed_model(serving_service_name = EMBEDDING_SERVING_SERVICE_NAME,
                           port = 30001)
    SystemLogger.info(" ✅ Serving embedding service ready!")


    # Init Postgres
    postgres_service = init_postgres()
    # Wait for postgres
    await wait_for_postgres(postgres_service._create_pool())
    # Create file store in Postgres
    await PostgresFileStore._create_table(postgres_service.pool)
    # Create Vector store in Postgres
    await PostgresVectorStore._create_table(postgres_service.pool)
    # Init Minio
    init_minio()
    # Logging
    SystemLogger.info(" ✅ Minio ready!")
    SystemLogger.info(f"Start services after :{round(time.perf_counter() - start,1)}s")