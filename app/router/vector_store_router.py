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
from app.startup import get_postgres_pool, get_qdrant_service, get_dense_embedding
# Config
from app.core.config import DENSE_MODEL_NAME
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
from mlflow.entities import SpanType, SpanEvent
# Enable logging
mlflow.config.enable_async_logging()

def _convert_timestamps_to_unix(record: dict) -> tuple:
    """Convert datetime timestamps from database record to Unix timestamps.
    
    Args:
        record: Dictionary containing timestamp fields from database
        
    Returns:
        Tuple of (created_at, last_active_at, expires_at) as Unix timestamps or None
    """
    created_at = record["created_at"]
    if hasattr(created_at, 'timestamp'):
        created_at = int(created_at.timestamp())

    last_active_at = record.get("last_active_at")
    if last_active_at and hasattr(last_active_at, 'timestamp'):
        last_active_at = int(last_active_at.timestamp())

    expires_at = record.get("expires_at")
    if expires_at and hasattr(expires_at, 'timestamp'):
        expires_at = int(expires_at.timestamp())
    
    return created_at, last_active_at, expires_at

def _calculate_file_counts(status: str) -> VectorStoreFileCounts:
    """Calculate file counts based on vector store status.
    
    Args:
        status: Current status of the vector store
        
    Returns:
        VectorStoreFileCounts with completed/failed counts
    """
    return VectorStoreFileCounts(
        completed=1 if status == "completed" else 0,
        failed=1 if status == "failed" else 0
    )

# Define router
vector_store_router = APIRouter()

@vector_store_router.post("/vector_stores", response_model=VectorStoreObject)
async def create_vector_store(request: VectorStoreCreateRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreObject:
    """
    ## Create a vector store.

    ### Args
    - `name` (str): The name of the vector store.
    - `description` (str, optional): A description for the vector store. Can be used to describe the vector store's purpose.
    - `file_ids` (List[str], optional): A list of File IDs that the vector store should use. Useful for tools like file_search that can access files.
    - `expires_after` (optional): The expiration policy for a vector store.
    - `metadata` (optional): Set of 16 key-value pairs that can be attached to an object
    - `chunking_strategy` (optional): The chunking strategy used to chunk the file(s).

    Reference: [OpenAI Create Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/create)

    """
    # Get current time in UTC for timestamps
    current_time = datetime.now(timezone.utc)
    created_at = current_time

    # Generate unique vector store ID
    vectorstore_id = generate_vectorstore_id()

    # Get PostgreSQL connection pool
    postgres_pool = get_postgres_pool()

    try:
        # Calculate expiration time and policy if specified
        expires_at = None
        expires_after = None
        if request.expires_after is not None:
            # Convert days to seconds for storage
            expires_after = timedelta(days = request.expires_after.days).total_seconds()
            # Calculate absolute expiration timestamp
            expires_at = created_at + timedelta(days = request.expires_after.days)

        # Count files that will be processed
        nums_in_progress_file = 0
        if request.file_ids is not None:
            nums_in_progress_file = len(request.file_ids)

        # Save vector store metadata to PostgreSQL database
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

        # Determine chunking strategy parameters
        # Default to auto chunking if no strategy specified
        if request.chunking_strategy is None:
            chunking_strategy = "auto"
            chunk_size = 800
            chunk_overlap = 400
        # Use static chunking parameters if specified
        elif request.chunking_strategy.type == "static":
            chunking_strategy = "static"
            chunk_size = request.chunking_strategy.static.max_chunk_size_tokens
            chunk_overlap = request.chunking_strategy.static.chunk_overlap_tokens
        # Use fuse chunking as fallback
        else:
            chunking_strategy = "fuse"
            chunk_size = 800
            chunk_overlap = 400

        # Start background processing of files using TaskIQ worker
        await process_vector_store_files.kiq(vectorstore_id = vectorstore_id,
                                             api_key = api_key,
                                             file_ids = request.file_ids,
                                             chunking_strategy = chunking_strategy,
                                             chunk_size = chunk_size,
                                             chunk_overlap = chunk_overlap)
        
        # Build and return response object from stored record
        return VectorStoreObject(id = vectorstore_id,
                                 name = request.name,
                                 created_at = int(created_at.timestamp()),
                                 last_active_at = int(created_at.timestamp()),
                                 expires_at = int(record.get("expires_at").timestamp()) if record.get("expires_at") is not None else None,
                                 expires_after = VectorStoreExpiresAfter(days = timedelta(seconds=int(expires_after)).days,
                                                                         anchor = "last_active_at") if expires_after is not None else None,
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
    ## Returns a list of vector stores.

    ### Args:
    - `limit` (int, optional): A limit on the number of objects to be returned. Limit can range between 1 and 100, and the default is 20
    - `order` (str, optional): Sort order by the created_at timestamp of the objects. asc for ascending order and desc for descending order.
    - `after` (str, optional): A cursor for use in pagination. after is an object ID that defines your place in the lis
    - `before` (str, optional): A cursor for use in pagination. before is an object ID that defines your place in the list.

    Reference: [OpenAI List Vector Stores API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/list)

    """
    postgres_pool = get_postgres_pool()
    
    try:
        # List vector stores from database
        result = await PostgresVectorStore.list_vector_stores(pool=postgres_pool,
                                                        api_key=api_key,
                                                        limit=query_object.limit,
                                                        order=query_object.order,
                                                after=query_object.after,
                                                before=query_object.before)
        
        # Convert records to VectorStoreObject format
        vector_stores = []
        for record in result["data"]:
            # Convert timestamps to Unix timestamps (integers)
            created_at, last_active_at, expires_at = _convert_timestamps_to_unix(record)

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
                                             file_counts=_calculate_file_counts(status),
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
    ## Retrieves a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store

    Reference: [OpenAI Retrieve Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/retrieve)

    """
    # Get module
    postgres_pool = get_postgres_pool()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    try:
        # Get vector store from database
        record = await PostgresVectorStore.get_by_id(pool=postgres_pool,
                                               vector_store_id=vector_store_id,
                                               api_key=api_key)

        # Convert timestamps to Unix timestamps (integers)
        created_at, last_active_at, expires_at = _convert_timestamps_to_unix(record)

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
                                 file_counts=_calculate_file_counts(status),
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
    ## Modifies a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to update
    - `name` (str, optional): The name of the vector store.
    - `metadata` (Dict[str, Any], optional): Set of 16 key-value pairs that can be attached to an object.

    Reference: [OpenAI Update Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/update)

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
        created_at, last_active_at, expires_at = _convert_timestamps_to_unix(record)

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
                                 file_counts=_calculate_file_counts(status),
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
    ## Delete a vector store.

    ### Args:
    - `vector_store_id` (str): The unique identifier of the vector store to delete

    Reference: [OpenAI Delete Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/delete)

    """
    # Get module
    postgres_pool = get_postgres_pool()
    qdrant_service = get_qdrant_service()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    # Check vector store existence
    await PostgresVectorStore._check_vector_store_existence(pool=postgres_pool,
                                                            vector_store_id=vector_store_id,
                                                            api_key=api_key)

    try:

        # Delete vector store metadata from PostgreSQL database
        await PostgresVectorStore.delete(pool=postgres_pool,
                                         vector_store_id=vector_store_id,
                                         api_key=api_key)
        # Delete the actual vector collection from Qdrant
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
    except Exception as e:
        SystemLogger.error(f"Error deleting vector store: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting vector store: {str(e)}"
        )

@vector_store_router.post("/vector_stores/{vector_store_id}/search",response_model = VectorStoreSearchResponse)
async def search_vector_store(vector_store_id: str,
                              search_request: VectorStoreSearchRequest,
                              api_key: str = Depends(verify_api_key)) -> VectorStoreSearchResponse:
    """
    ## Search a vector store for relevant chunks based on a query and file attributes filter.

    ### Args
    - `vector_store_id` (str): The ID of the vector store to search in.
    - `query` (Union[str, List[str]]): A query string for a search
    - `filters` (Optional[Union[ComparisonFilter, CompoundFilter]]): A filter to apply based on file attributes.
    - `max_num_results` (int, optional): The maximum number of results to return. This number should be between 1 and 50 inclusive.
    - `ranking_options` (Optional[RankingOptions]): Ranking options for search.
    
    ### Returns
    - `VectorStoreSearchResponse`: A response containing the search results.

    Reference: [OpenAI Search Vector Store API](https://developers.openai.com/api/reference/python/resources/vector_stores/methods/search)

    """
    # Get Qdrant service
    qdrant_service = get_qdrant_service()
    # Get Postgres service
    postgres_pool = get_postgres_pool()
    # Validate vector store id
    validate_vector_store_prefix(vector_store_id)

    # Check vector store existence
    await PostgresVectorStore._check_vector_store_existence(pool=postgres_pool,
                                                            vector_store_id=vector_store_id,
                                                            api_key=api_key)

    try:
        with mlflow.start_span(name="/v1/vector_stores/{vector_store_id}/search", span_type=SpanType.UNKNOWN) as span:
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

            # Normalize query to list format - handle both single string and list inputs
            # For lists, only use the first query for now
            queries = [search_request.query] if isinstance(search_request.query, str) else search_request.query[:1]

            # Embedding span
            with mlflow.start_span(name="embedding", span_type=SpanType.EMBEDDING) as span:
                # Set inputs
                span.set_inputs({"queries_batch_size": len(queries)})
                # Set attribute
                span.set_attributes({"embedding_model_name": DENSE_MODEL_NAME})
                # Embed the queries
                queries_vectors = await get_dense_embedding(queries)
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

                # Check if vector store collection exists in Qdrant
                vector_store_existence = await qdrant_service.client.collection_exists(collection_name = vector_store_id)

                # Handle case when vector store doesn't exist
                if not vector_store_existence:
                    # Return empty results when vector store is not found
                    data = []
                    displayed_results = []
                    # Log event for monitoring
                    span.add_event(event = SpanEvent(name = "Vector store collection not found"))
                else:
                    # Vector store exists - perform search
                    retrieved_results = await qdrant_vector_store.retrieve(query_vectors = queries_vectors,
                                                                           query_filter = qdrant_filter,
                                                                           limit = search_request.max_num_results)

                    # Extract search results for logging/display
                    displayed_results = []
                    for point in retrieved_results[0].points:
                        displayed_results.append({"chunk_id": point.id,
                                                  "score": point.score,
                                                  "metadata": point.payload.get("metadata")})

                    # Convert results to API response format
                    data = convert_query_response_to_search_results(retrieved_results)

                # Set attribute
                span.set_attributes({"vector_store_type": "qdrant",
                                     "vector_store_id": vector_store_id})
                # Set output
                span.set_outputs({"results": displayed_results})

                # Add tag
                mlflow.update_current_trace(tags={"vector_store_id": vector_store_id,
                                                  "token": api_key})
            # Update state
            mlflow.flush_async_logging()
            # Return
            return VectorStoreSearchResponse(search_query=search_request.query,
                                             data=data,
                                             has_more=len(data) >= search_request.max_num_results)

    except (asyncpg.PostgresError, socket.gaierror) as e:
        SystemLogger.error(e)
        raise PostgresConnectionException()
    except Exception as e:
        SystemLogger.error(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error searching vector store: {str(e)}"
        )
