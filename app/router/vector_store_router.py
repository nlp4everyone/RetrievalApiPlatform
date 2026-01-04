from fastapi import APIRouter, Depends, HTTPException
# Schemas
from app.schemas.vector_store import *
from app.schemas.file.types import UploadingStatus
# Security
from app.security.auth import verify_api_key
# DB
from app.db.postgres import PostgresVectorStore
from app.db.qdrant import AsyncQdrantVectorStore
from app.startup import get_postgres_pool, get_qdrant_service
# Helper
from app.utils.key_generator import generate_vectorstore_id
# Exceptions
from app.exceptions.postgres import PostgresConnectionException
# Logger
from loggers import SystemLogger
# Other components
from datetime import datetime, timezone, timedelta
import asyncpg, socket
from taskiq_worker import process_vector_store_files

# Define router
vector_store_router = APIRouter()

@vector_store_router.post("/vector_stores", response_model=VectorStoreObject)
async def create_vector_store(request: VectorStoreCreateRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Create a new vector store.
    This endpoint creates a new vector store with the provided configuration and returns its representation.
    The endpoint follows the OpenAI `/vector_stores` create endpoint specification.

    ### Args
    - `name` (str): A name for the vector store
    - `description` (str, optional): A description for the vector store
    - `file_ids` (List[str], optional): List of file IDs to include in the vector store
    - `expires_after` (optional): Expiration configuration
    - `metadata` (optional): Additional metadata
    - `chunking_strategy` (optional): Strategy for chunking documents
    """
    # Get current time in UTC
    current_time = datetime.now(timezone.utc)
    created_at = current_time

    # Generate id
    vectorstore_id = generate_vectorstore_id()

    # Persist to Postgres
    postgres_pool = get_postgres_pool()

    try:
        # Define expires_after and expires_at
        expires_at = None
        expires_after = None
        # If request after is not None
        if request.expires_after is not None:
            expires_after = timedelta(days = request.expires_after.days).total_seconds()
            expires_at = created_at + timedelta(days = request.expires_after.days)

        # Define process file
        nums_in_progress_file = 0
        if request.file_ids is not None:
            nums_in_progress_file = len(request.file_ids)

        # Save to database
        record = await PostgresVectorStore.create(pool = postgres_pool,
                                                  id = vectorstore_id,
                                                  api_key = api_key,
                                                  name = request.name,
                                                  description = request.description,
                                                  created_at = created_at,
                                                  last_active_at = created_at,
                                                  status = UploadingStatus.IN_PROGRESS,
                                                  usage_bytes = 0,
                                                  metadata = request.metadata,
                                                  expires_at = expires_at,  # Pass the datetime object directly
                                                  expires_after = expires_after,
                                                  chunking_strategy = request.chunking_strategy.model_dump() if request.chunking_strategy else None,
                                                  vector_store_type = VectorStoreType.QDRANT)

        # Auto case
        if request.chunking_strategy is None:
            chunking_strategy = "auto"
            chunk_size = 800
            chunk_overlap = 400
        elif request.chunking_strategy.type == "static":
            chunking_strategy = "static"
            chunk_size = request.chunking_strategy.static.max_chunk_size_tokens
            chunk_overlap = request.chunking_strategy.static.chunk_overlap_tokens
        else:
            chunking_strategy = "fuse"
            chunk_size = 800
            chunk_overlap = 400

        # Run in background
        await process_vector_store_files.kiq(vectorstore_id = vectorstore_id,
                                             api_key = api_key,
                                             file_ids = request.file_ids,
                                             chunking_strategy = chunking_strategy,
                                             chunk_size = chunk_size,
                                             chunk_overlap = chunk_overlap)

        # Convert expires_after to dict
        expires_after_days = timedelta(seconds=int(expires_after)).days
        
        # Build response from stored record
        return VectorStoreObject(id = vectorstore_id,
                                 name = request.name,
                                 created_at = int(created_at.timestamp()),
                                 last_active_at = int(created_at.timestamp()),
                                 expires_at = int(record.get("expires_at").timestamp()),
                                 expires_after = VectorStoreExpiresAfter(days = timedelta(seconds=int(expires_after)).days,
                                                                         anchor = "last_active_at"),
                                 file_counts = VectorStoreFileCounts(in_progress = nums_in_progress_file,
                                                                     total = nums_in_progress_file),
                                 metadata = record.get("metadata"),
                                 status = "in_progress",
                                 usage_bytes = 0)
    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()

@vector_store_router.get("/vector_stores", response_model=ListVectorStoreObject)
async def list_vector_stores(query_object: VectorStoreQueryRequest = Depends(),
                             api_key: str = Depends(verify_api_key)) -> ListVectorStoreObject:
    """
    ## List vector stores with pagination.
    Retrieves a paginated list of vector stores for the authenticated API key.

    ### Args:
    - `limit` (int, optional): Maximum number of results to return
    - `order` (str, optional): Sort order ('asc' or 'desc')
    - `after` (str, optional): Cursor for pagination (ID of last item from previous page)
    - `before` (str, optional): Cursor for pagination (ID of first item from next page)
    """
    postgres_pool = get_postgres_pool()
    
    try:
        # List vector stores from database
        result = await PostgresVectorStore.list(pool=postgres_pool,
                                                api_key=api_key,
                                                limit=query_object.limit,
                                                order=query_object.order,
                                                after=query_object.after,
                                                before=query_object.before)
        
        # Convert records to VectorStoreObject format
        vector_stores = []
        for record in result["data"]:
            # Convert timestamps to Unix timestamps (integers)
            created_at = record["created_at"]
            if hasattr(created_at, 'timestamp'):
                created_at = int(created_at.timestamp())

            last_active_at = record.get("last_active_at")
            if last_active_at and hasattr(last_active_at, 'timestamp'):
                last_active_at = int(last_active_at.timestamp())

            expires_at = record.get("expires_at")
            if expires_at and hasattr(expires_at, 'timestamp'):
                expires_at = int(expires_at.timestamp())

            # Convert expires_after to VectorStoreExpiresAfter if it exists
            expires_after = record.get("expires_after")
            if expires_after and isinstance(expires_after, int):
                expires_after = VectorStoreExpiresAfter(
                    anchor="last_active_at",
                    days=expires_after // 86400  # Convert seconds to days
                )

            status = record.get("status")
            # Create vector store object with properly formatted fields
            vector_store = VectorStoreObject(id=record["id"],
                                             name=record.get("name"),
                                             created_at=created_at,
                                             last_active_at=last_active_at,
                                             expires_at=expires_at,
                                             expires_after=expires_after,
                                             file_counts=VectorStoreFileCounts(completed=1 if status == "completed" else 0,
                                                                               failed=1 if status == "failed" else 0),
                                             metadata=record.get("metadata"),
                                             status=record.get("status"),
                                             usage_bytes=record.get("usage_bytes", 0))
            vector_stores.append(vector_store)
            #vector_stores.append(vector_store)

        # Return
        return ListVectorStoreObject(data = vector_stores,
                                     first_id = result["first_id"],
                                     last_id = result["last_id"],
                                     has_more = result["has_more"])
        
    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()


@vector_store_router.get("/vector_stores/{vector_store_id}", response_model=VectorStoreObject)
async def get_vector_store(
    vector_store_id: str,
    api_key: str = Depends(verify_api_key)
) -> VectorStoreObject:
    """
    ## Retrieve a specific vector store by ID.
    Fetches the details of a vector store including its metadata, status, and file counts.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store
    """
    postgres_pool = get_postgres_pool()

    try:
        # Get vector store from database
        record = await PostgresVectorStore.get(pool=postgres_pool,
                                               vector_store_id=vector_store_id,
                                               api_key=api_key)

        # Check if vector store exists
        if not record:
            raise HTTPException(status_code=404, detail="Vector store not found")

        # Convert timestamps to Unix timestamps (integers)
        created_at = record["created_at"]
        if hasattr(created_at, 'timestamp'):
            created_at = int(created_at.timestamp())

        last_active_at = record.get("last_active_at")
        if last_active_at and hasattr(last_active_at, 'timestamp'):
            last_active_at = int(last_active_at.timestamp())

        expires_at = record.get("expires_at")
        if expires_at and hasattr(expires_at, 'timestamp'):
            expires_at = int(expires_at.timestamp())

        # Convert expires_after to VectorStoreExpiresAfter if it exists
        expires_after = record.get("expires_after")
        if expires_after and isinstance(expires_after, int):
            expires_after = VectorStoreExpiresAfter(anchor="last_active_at",
                                                    days=expires_after // 86400)

        status = record.get("status")
        # Create and return vector store object with properly formatted fields
        return VectorStoreObject(id=record["id"],
                                 name=record.get("name"),
                                 created_at=created_at,
                                 last_active_at=last_active_at,
                                 expires_at=expires_at,
                                 expires_after=expires_after,
                                 file_counts=VectorStoreFileCounts(completed = 1 if status == "completed" else 0,
                                                                   failed = 1 if status == "failed" else 0),
                                 metadata=record.get("metadata"),
                                 status=record.get("status"),
                                 usage_bytes=record.get("usage_bytes", 0))

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()


@vector_store_router.post("/vector_stores/{vector_store_id}", response_model=VectorStoreObject)
async def modify_vector_store(vector_store_id: str,
                              request: VectorStoreModifyRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Update a vector store's metadata.
    Modifies the specified vector store's name and metadata. Only the provided fields will be updated.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to update
    - `name` (str, optional): New name for the vector store
    - `metadata` (Dict[str, Any], optional): New metadata to merge with existing metadata
    """
    postgres_pool = get_postgres_pool()

    try:
        # Update the record
        record = await PostgresVectorStore.update(pool=postgres_pool,
                                                  vector_store_id=vector_store_id,
                                                  api_key=api_key,
                                                  name=request.name,
                                                  metadata=request.metadata)

        # Convert timestamps to Unix timestamps (integers)
        created_at = record["created_at"]
        if hasattr(created_at, 'timestamp'):
            created_at = int(created_at.timestamp())

        last_active_at = record.get("last_active_at")
        if last_active_at and hasattr(last_active_at, 'timestamp'):
            last_active_at = int(last_active_at.timestamp())

        expires_at = record.get("expires_at")
        if expires_at and hasattr(expires_at, 'timestamp'):
            expires_at = int(expires_at.timestamp())

        # Convert expires_after to VectorStoreExpiresAfter if it exists
        expires_after = record.get("expires_after")
        if expires_after and isinstance(expires_after, int):
            expires_after = VectorStoreExpiresAfter(
                anchor="last_active_at",
                days=expires_after // 86400  # Convert seconds to days
            )

        status = record.get("status")
        # Return object with properly formatted fields
        return VectorStoreObject(id=record["id"],
                                 name=record.get("name"),
                                 created_at=created_at,
                                 last_active_at=last_active_at,
                                 expires_at=expires_at,
                                 expires_after=expires_after,
                                 file_counts=VectorStoreFileCounts(completed=1 if status == "completed" else 0,
                                                                   failed=1 if status == "failed" else 0),
                                 metadata=record.get("metadata"),
                                 status=record.get("status"),
                                 usage_bytes=record.get("usage_bytes", 0))

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()


@vector_store_router.delete("/vector_stores/{vector_store_id}", response_model=VectorStoreDeletion)
async def delete_vector_store(vector_store_id: str,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreDeletion:
    """
    ## Permanently delete a vector store.
    This action cannot be undone. All data associated with the vector store,
    including its vector embeddings and metadata, will be permanently removed.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to delete
    """
    postgres_pool = get_postgres_pool()
    qdrant_service = get_qdrant_service()

    try:
        # Delete the record
        await PostgresVectorStore.delete(pool=postgres_pool,
                                         vector_store_id=vector_store_id,
                                         api_key=api_key)
        # Delete to vector store
        qdrant_vector_store = AsyncQdrantVectorStore(collection_name = vector_store_id,
                                                     client=qdrant_service.client)
        await qdrant_vector_store.delete_collection()

        # Return deletion confirmation
        return VectorStoreDeletion(id=vector_store_id,
                                   object="vector_store.deleted",
                                   deleted=True)

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()
