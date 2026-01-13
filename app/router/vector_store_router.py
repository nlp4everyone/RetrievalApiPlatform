from fastapi import APIRouter, Depends, HTTPException, status
# Schemas
from app.schemas.vector_store import *
from app.schemas.vector_store.requests import *
from app.schemas.vector_store.responses import *
from app.schemas.file.types import UploadingStatus
# Security
from app.security.auth import verify_api_key
# DB
from app.db.postgres import PostgresVectorStore
from app.db.qdrant import AsyncQdrantVectorStore
from app.startup import get_postgres_pool, get_qdrant_service, get_embed_model
# Helper
from app.utils.key_generator import generate_vectorstore_id
from app.utils.vector_store.utils import (convert_query_response_to_search_results,
                                          validate_vector_store_prefix)
# Exceptions
from app.exceptions.postgres import PostgresConnectionException
# Logger
from loggers import SystemLogger
# Other components
from datetime import datetime, timezone, timedelta
import asyncpg, socket, mlflow
# TaskIQ worker
from taskiq_worker import process_vector_store_files
# Qdrant component
from qdrant_client import models
from mlflow.entities import SpanType
# Enable logging
mlflow.config.enable_async_logging()

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

        # Return
        return ListVectorStoreObject(data = vector_stores,
                                     first_id = result["first_id"],
                                     last_id = result["last_id"],
                                     has_more = result["has_more"])
        
    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()


@vector_store_router.get("/vector_stores/{vector_store_id}", response_model=VectorStoreObject)
async def get_vector_store(vector_store_id: str,
                           api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Retrieve a specific vector store by ID.
    Fetches the details of a vector store including its metadata, status, and file counts.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store
    """
    # Get module
    postgres_pool = get_postgres_pool()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    try:
        # Get vector store from database
        record = await PostgresVectorStore.get(pool=postgres_pool,
                                               vector_store_id=vector_store_id,
                                               api_key=api_key)


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
    # Get module
    postgres_pool = get_postgres_pool()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

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
    # Get module
    postgres_pool = get_postgres_pool()
    qdrant_service = get_qdrant_service()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    # Check vector store existance
    await PostgresVectorStore._check_vector_store_existance(pool=postgres_pool,
                                                            vector_store_id=vector_store_id,
                                                            api_key=api_key)

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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching vector store: {str(e)}"
        )

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()

@vector_store_router.post("/vector_stores/{vector_store_id}/search",response_model = VectorStoreSearchResponse)
async def search_vector_store(vector_store_id: str,
                              search_request: VectorStoreSearchRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreSearchResponse:
    """
    ## Search a vector store.
    
    This endpoint searches for documents in a vector store based on a query and optional filters.
    It supports semantic search with filtering capabilities.

    ### Args
    - `vector_store_id` (str): The ID of the vector store to search in.
    - `query` (Union[str, List[str]]): The query string or list of query strings to search for.
    - `filters` (Optional[Union[ComparisonFilter, CompoundFilter]]): Optional filters to apply to the search.
    - `max_num_results` (int, optional): Maximum number of results to return (1-50). Defaults to 10.
    - `ranking_options` (Optional[RankingOptions]): Options for controlling search result ranking.
    
    ### Returns
    - `VectorStoreSearchResponse`: A response containing the search results.
    """
    # Get Qdrant service
    qdrant_service = get_qdrant_service()
    # Embed model
    embed_model = get_embed_model()
    # Get Postgres service
    postgres_pool = get_postgres_pool()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    # Check vector store existance
    await PostgresVectorStore._check_vector_store_existance(pool=postgres_pool,
                                                            vector_store_id=vector_store_id,
                                                            api_key=api_key)

    try:
        with mlflow.start_span(name="POST /v1/vector_stores/{vector_store_id}/search", span_type=SpanType.UNKNOWN) as span:
            # # Check if collection exists
            # collection_exists = await qdrant_service.client.collection_exists(collection_name = vector_store_id)
            # # If not existed
            # if not collection_exists:
            #     # Raise exception
            #     raise VectorStoreNotFoundException(vector_store_id = vector_store_id)
            # Log
            # Set inputs
            span.set_inputs({"search_request": search_request.model_dump()})

            qdrant_vector_store = AsyncQdrantVectorStore(collection_name = vector_store_id,
                                                         client = qdrant_service.client)
            # Convert filters to Qdrant format if provided
            qdrant_filter = None
            # if search_request.filters:
            #     qdrant_filter = _normalize_qdrant_filter(search_request.filters)

            # Prepare search parameters
            search_params = models.SearchParams(
                quantization=models.QuantizationSearchParams(
                    ignore=search_request.ranking_options.ranker == "none" if search_request.ranking_options else False,
                    rescore=search_request.ranking_options.ranker == "auto" if search_request.ranking_options else True,
                )
            ) if search_request.ranking_options else None

            # Convert query to list if it's a single string ( If it's a list, remain only first element)
            queries = [search_request.query] if isinstance(search_request.query, str) else search_request.query[:1]

            # Embedding span
            with mlflow.start_span(name="embedding", span_type=SpanType.EMBEDDING) as span:
                # Set inputs
                span.set_inputs({"queries_batch_size": len(queries)})
                # Set attribute
                span.set_attributes({"embedding_model_name": embed_model.model})
                # Embed the queries
                queries_vectors = await embed_model.aembed_documents(queries)
                # Define embedding dims
                embedding_dims = len(queries_vectors[0])
                embedding_batch_size = len(queries_vectors)
                # Set output
                span.set_outputs({"embedding_batch_size": embedding_batch_size,
                                  "embedding_dims": embedding_dims})

            # Retrieval span
            with mlflow.start_span(name="retrieve", span_type=SpanType.RETRIEVER) as span:
                # Set inputs
                span.set_inputs({"embedding_batch_size": embedding_batch_size,
                                 "embedding_dims": embedding_dims,
                                 "max_num_results": search_request.max_num_results})
                # Perform search
                retrieved_results = await qdrant_vector_store.retrieve(query_vectors = queries_vectors,
                                                                       query_filter = qdrant_filter,
                                                                       limit = search_request.max_num_results)

                # Construct data
                displayed_results = []
                for point in retrieved_results[0].points:
                    displayed_results.append({"chunk_id": point.id,
                                              "score": point.score,
                                              "metadata": point.payload.get("metadata")})

                # Set attribute
                span.set_attributes({"vector_store_type": "qdrant",
                                     "vector_store_id": vector_store_id})
                # Set output
                span.set_outputs({"results": displayed_results})

            # Convert results to response format
            data = convert_query_response_to_search_results(retrieved_results)
            # Add tag
            mlflow.update_current_trace(tags={"vector_store_id": vector_store_id,
                                              "token": api_key})
            # Update state
            mlflow.flush_async_logging()
            # Return
            return VectorStoreSearchResponse(search_query=search_request.query,
                                             data=data,
                                             has_more=len(data) >= search_request.max_num_results)

    except Exception as e:
        SystemLogger.error(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching vector store: {str(e)}"
        )

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()
