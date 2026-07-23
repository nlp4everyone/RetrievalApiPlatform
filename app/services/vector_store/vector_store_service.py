from typing import Any, Optional
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
import asyncpg, socket, json

# Schemas
from app.schemas.vector_store import *
from app.schemas.vector_store.requests import *
from app.schemas.vector_store.responses import *
from app.schemas.file.types import UploadingStatus

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
from app.utils.datetime_utils import convert_to_unix_timestamp

# Exceptions
from app.exceptions import AppBaseException
from app.exceptions.postgres import PostgresConnectionException

# Logger
from loggers import SystemLogger

# Request context
from app.core.request_context import request_id_ctx

# TaskIQ worker
from taskiq_worker import process_vector_store_files

# Qdrant component
from qdrant_client import models

# Tracing
from app.core.tracing import traced_span


class VectorStoreService:
    """
    Service class for vector store management operations.
    
    This class provides static methods for vector store-related business logic,
    separating concerns from HTTP handling and database/storage implementations.
    """
    
    
    @staticmethod
    def _calculate_file_counts(status: str) -> VectorStoreFileCounts:
        """
        Calculate file counts based on vector store status.
        
        Args:
            status: Current status of the vector store
            
        Returns:
            VectorStoreFileCounts with completed/failed counts
        """
        return VectorStoreFileCounts(
            completed=1 if status == "completed" else 0,
            failed=1 if status == "failed" else 0
        )
    
    @staticmethod
    def _convert_expires_after(expires_after: Optional[int]) -> Optional[VectorStoreExpiresAfter]:
        """
        Convert expires_after from seconds to VectorStoreExpiresAfter object.
        
        Args:
            expires_after: Expiration time in seconds, or None
            
        Returns:
            VectorStoreExpiresAfter object or None
        """
        if expires_after and isinstance(expires_after, int):
            return VectorStoreExpiresAfter(
                anchor="last_active_at",
                days=expires_after // 86400  # Convert seconds to days
            )
        return None
    
    @staticmethod
    def _build_vector_store_object(record: dict[str, Any]) -> VectorStoreObject:
        """
        Build VectorStoreObject from database record.
        
        Args:
            record: Database record containing vector store data
            
        Returns:
            VectorStoreObject with properly formatted fields
        """
        # Convert timestamps to Unix timestamps
        created_at = convert_to_unix_timestamp(record["created_at"])
        last_active_at = convert_to_unix_timestamp(record.get("last_active_at"))
        expires_at = convert_to_unix_timestamp(record.get("expires_at"))
        
        # Convert expires_after to VectorStoreExpiresAfter
        expires_after = VectorStoreService._convert_expires_after(record.get("expires_after"))
        
        status = record.get("status")
        
        return VectorStoreObject(
            id=record["id"],
            name=record.get("name"),
            created_at=created_at,
            last_active_at=last_active_at,
            expires_at=expires_at,
            expires_after=expires_after,
            file_counts=VectorStoreService._calculate_file_counts(status),
            metadata=record.get("metadata"),
            status=record.get("status"),
            usage_bytes=record.get("usage_bytes", 0)
        )
    
    @staticmethod
    async def create(request: VectorStoreCreateRequest,
                     api_key: str) -> VectorStoreObject:
        """
        Create a vector store with optional file attachments.
        
        This method handles the complete vector store creation workflow including
        validation, ID generation, expiration calculation, chunking strategy
        determination, and background task orchestration.
        
        Args:
            request: Vector store creation request with name, file_ids, chunking strategy, etc.
            api_key: API key identifying the user creating the vector store
            
        Returns:
            VectorStoreObject containing created vector store metadata
            
        Raises:
            PostgresConnectionException: If database connection fails
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
                expires_after = timedelta(days=request.expires_after.days).total_seconds()
                # Calculate absolute expiration timestamp
                expires_at = created_at + timedelta(days=request.expires_after.days)

            # Count files that will be processed
            nums_in_progress_file = 0
            if request.file_ids is not None:
                nums_in_progress_file = len(request.file_ids)

            # Save vector store metadata to PostgreSQL database
            record = await PostgresVectorStore.create(
                pool=postgres_pool,
                id=vectorstore_id,
                api_key=api_key,
                name=request.name,
                description=request.description,
                created_at=created_at,
                last_active_at=created_at,
                status=UploadingStatus.IN_PROGRESS,
                usage_bytes=0,
                metadata=request.metadata,
                expires_at=expires_at,
                expires_after=expires_after,
                chunking_strategy=request.chunking_strategy.model_dump() if request.chunking_strategy else None,
                vector_store_type=VectorStoreType.QDRANT
            )

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
            await process_vector_store_files.kiq(
                vectorstore_id=vectorstore_id,
                api_key=api_key,
                file_ids=request.file_ids,
                chunking_strategy=chunking_strategy,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                request_id=request_id_ctx.get()
            )

            SystemLogger.info(f"Vector store created: {vectorstore_id} ({nums_in_progress_file} file(s) queued)")

            # Build and return response object
            return VectorStoreObject(
                id=vectorstore_id,
                name=request.name,
                created_at=convert_to_unix_timestamp(created_at),
                last_active_at=convert_to_unix_timestamp(created_at),
                expires_at=convert_to_unix_timestamp(record.get("expires_at")),
                expires_after=VectorStoreExpiresAfter(
                    days=timedelta(seconds=int(expires_after)).days,
                    anchor="last_active_at"
                ) if expires_after is not None else None,
                file_counts=VectorStoreFileCounts(
                    in_progress=nums_in_progress_file,
                    total=nums_in_progress_file
                ),
                metadata=record.get("metadata"),
                status="in_progress",
                usage_bytes=0
            )
        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while creating vector store: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error creating vector store: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating vector store: {str(e)}"
            )

    @staticmethod
    async def list(api_key: str,
                   query_object: VectorStoreQueryRequest) -> ListVectorStoreObject:
        """
        List vector stores for an API key with optional filtering and pagination.
        
        Args:
            api_key: API key to filter vector stores by ownership
            query_object: Query parameters including limit, order, after, before cursors
            
        Returns:
            ListVectorStoreObject containing paginated list of vector stores
            
        Raises:
            PostgresConnectionException: If database connection fails
        """
        postgres_pool = get_postgres_pool()
        
        try:
            # List vector stores from database
            result = await PostgresVectorStore.list_vector_stores(
                pool=postgres_pool,
                api_key=api_key,
                limit=query_object.limit,
                order=query_object.order,
                after=query_object.after,
                before=query_object.before
            )
            
            # Convert records to VectorStoreObject format
            vector_stores = []
            for record in result["data"]:
                vector_store = VectorStoreService._build_vector_store_object(record)
                vector_stores.append(vector_store)

            SystemLogger.debug(f"Listed {len(vector_stores)} vector store(s)")

            # Return paginated response
            return ListVectorStoreObject(
                data=vector_stores,
                first_id=result["first_id"],
                last_id=result["last_id"],
                has_more=result["has_more"]
            )

        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while listing vector stores: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error listing vector stores: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error listing vector stores: {str(e)}"
            )

    @staticmethod
    async def get(vector_store_id: str,
                  api_key: str) -> VectorStoreObject:
        """
        Retrieve a single vector store by its ID.
        
        Args:
            vector_store_id: Unique vector store identifier
            api_key: API key for ownership validation
            
        Returns:
            VectorStoreObject containing vector store metadata
            
        Raises:
            PostgresConnectionException: If database connection fails
        """
        # Validate vector store id
        validate_vector_store_prefix(vector_store_id)
        
        postgres_pool = get_postgres_pool()

        try:
            # Get vector store from database
            record = await PostgresVectorStore.get_by_id(
                pool=postgres_pool,
                vector_store_id=vector_store_id,
                api_key=api_key
            )

            SystemLogger.debug(f"Vector store retrieved: {vector_store_id}")

            # Build and return vector store object
            return VectorStoreService._build_vector_store_object(record)

        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while getting vector store {vector_store_id}: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error getting vector store {vector_store_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error getting vector store: {str(e)}"
            )

    @staticmethod
    async def modify(vector_store_id: str,
                     request: VectorStoreModifyRequest,
                     api_key: str) -> VectorStoreObject:
        """
        Modify a vector store's name or metadata.
        
        Args:
            vector_store_id: Unique vector store identifier
            request: Modification request with optional name and metadata
            api_key: API key for ownership validation
            
        Returns:
            VectorStoreObject containing updated vector store metadata
            
        Raises:
            PostgresConnectionException: If database connection fails
        """
        # Validate vector store id
        validate_vector_store_prefix(vector_store_id)
        
        postgres_pool = get_postgres_pool()

        try:
            # Update the record
            record = await PostgresVectorStore.update(
                pool=postgres_pool,
                vector_store_id=vector_store_id,
                api_key=api_key,
                name=request.name,
                metadata=request.metadata
            )

            SystemLogger.info(f"Vector store modified: {vector_store_id}")

            # Build and return updated vector store object
            return VectorStoreService._build_vector_store_object(record)

        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while modifying vector store {vector_store_id}: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error modifying vector store {vector_store_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error modifying vector store: {str(e)}"
            )

    @staticmethod
    async def delete(vector_store_id: str,
                     api_key: str) -> VectorStoreDeletion:
        """
        Delete a vector store from both PostgreSQL and Qdrant.
        
        This method performs a cascading deletion: first removes the database record
        (which includes ownership validation), then deletes the actual vector collection
        from Qdrant storage.
        
        Args:
            vector_store_id: Unique vector store identifier
            api_key: API key for ownership validation
            
        Returns:
            VectorStoreDeletion containing deletion confirmation
            
        Raises:
            PostgresConnectionException: If database connection fails
            HTTPException: If vector store deletion fails
        """
        # Validate vector store id
        validate_vector_store_prefix(vector_store_id)
        
        postgres_pool = get_postgres_pool()
        qdrant_service = get_qdrant_service()

        # Check vector store existence
        await PostgresVectorStore._check_vector_store_existence(
            pool=postgres_pool,
            vector_store_id=vector_store_id,
            api_key=api_key
        )

        try:
            # Delete vector store metadata from PostgreSQL database
            await PostgresVectorStore.delete(
                pool=postgres_pool,
                vector_store_id=vector_store_id,
                api_key=api_key
            )
            
            # Delete the actual vector collection from Qdrant
            qdrant_vector_store = AsyncQdrantVectorStore(
                collection_name=vector_store_id,
                client=qdrant_service.client
            )
            await qdrant_vector_store.delete_collection()

            SystemLogger.info(f"Vector store deleted: {vector_store_id}")

            # Return deletion confirmation
            return VectorStoreDeletion(
                id=vector_store_id,
                object="vector_store.deleted",
                deleted=True
            )
        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while deleting vector store {vector_store_id}: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error deleting vector store {vector_store_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error deleting vector store: {str(e)}"
            )
    
    @staticmethod
    async def search(vector_store_id: str,
                     search_request: VectorStoreSearchRequest,
                     api_key: str) -> VectorStoreSearchResponse:
        """
        Search a vector store for relevant chunks based on a query.
        
        This method handles the complete search workflow including MLflow tracing,
        embedding generation, Qdrant retrieval, and result formatting.
        
        Args:
            vector_store_id: ID of the vector store to search in
            search_request: Search request with query, filters, max_num_results, ranking_options
            api_key: API key for ownership validation
            
        Returns:
            VectorStoreSearchResponse containing search results
            
        Raises:
            PostgresConnectionException: If database connection fails
            HTTPException: If search operation fails
        """
        # Validate vector store id
        validate_vector_store_prefix(vector_store_id)
        
        qdrant_service = get_qdrant_service()
        postgres_pool = get_postgres_pool()

        # Check vector store existence
        await PostgresVectorStore._check_vector_store_existence(
            pool=postgres_pool,
            vector_store_id=vector_store_id,
            api_key=api_key
        )

        # TODO: filters not applied yet - _normalize_qdrant_filter is not implemented.
        qdrant_filter = None
        # if search_request.filters:
        #     qdrant_filter = _normalize_qdrant_filter(search_request.filters)

        # TODO: not passed into retrieve() below - ranking_options has no effect yet.
        search_params = models.SearchParams(
            quantization=models.QuantizationSearchParams(
                ignore=search_request.ranking_options.ranker == "none" if search_request.ranking_options else False,
                rescore=search_request.ranking_options.ranker == "auto" if search_request.ranking_options else True,
            )
        ) if search_request.ranking_options else None

        try:
            with traced_span(name=f"/v1/vector_stores/{vector_store_id}/search",
                             attributes={"langfuse.user.id": api_key,
                                         "langfuse.trace.tags": ["vector_store_search"],
                                         "langfuse.trace.metadata.vector_store_id": vector_store_id,
                                         "langfuse.trace.input": json.dumps({
                                             "query": search_request.query,
                                             "max_num_results": search_request.max_num_results})}) as search_span:
                search_span.set_attribute("vector_store.id", vector_store_id)

                # NOTE: only the first query is used when a list is given.
                queries = [search_request.query] if isinstance(search_request.query, str) else search_request.query[:1]

                # Embedding span
                with traced_span(name="embedding",
                                 attributes={"langfuse.observation.type": "embedding",
                                             "langfuse.observation.input": json.dumps({
                                                 "query": search_request.query})}) as embed_span:
                    # Embed the queries
                    queries_vectors = await get_dense_embedding(queries)
                    # Define embedding dims
                    embedding_dims = len(queries_vectors[0])
                    embedding_batch_size = len(queries_vectors)
                    # Set output attributes
                    embed_span.set_attribute("embedding.num_queries", embedding_batch_size)
                    embed_span.set_attribute("embedding.model", DENSE_MODEL_NAME)
                    embed_span.set_attribute("embedding.dims", embedding_dims)

                # Retrieval span
                with traced_span(name="retrieve",
                                 attributes={"langfuse.observation.type": "retriever",
                                             "langfuse.observation.metadata.vector_store_id": vector_store_id,
                                             "langfuse.observation.metadata.max_num_results": search_request.max_num_results}) as retrieve_span:
                    retrieve_span.set_attribute("vector_store.id", vector_store_id)
                    retrieve_span.set_attribute("vector_store.type", "qdrant")
                    # Check if vector store collection exists in Qdrant
                    vector_store_existence = await qdrant_service.client.collection_exists(
                        collection_name=vector_store_id
                    )
                    retrieve_span.set_attribute("vector_store.collection_exists", vector_store_existence)
                    retrieve_span.set_attribute("embedding.dims", embedding_dims)

                    # Handle case when vector store doesn't exist
                    if not vector_store_existence:
                        # Return empty results when vector store is not found
                        data = []
                        displayed_results = []
                        # Log event for monitoring
                        retrieve_span.add_event("vector_store_collection_not_found")
                    else:
                        # Vector store exists - perform search
                        qdrant_vector_store = AsyncQdrantVectorStore(
                            collection_name=vector_store_id,
                            client=qdrant_service.client
                        )
                        # TODO: pass search_params/score_threshold once wired (see TODO above)
                        retrieved_results = await qdrant_vector_store.retrieve(
                            query_vectors=queries_vectors,
                            query_filter=qdrant_filter,
                            limit=search_request.max_num_results
                        )

                        # chunk_id + score only for tracing - no metadata/content
                        displayed_results = [
                            {"chunk_id": point.id, "score": point.score}
                            for point in retrieved_results[0].points
                        ]

                        # Convert results to API response format
                        data = convert_query_response_to_search_results(retrieved_results)

                    # Set output attributes
                    retrieve_span.set_attribute("retrieve.result_count", len(displayed_results))
                    if displayed_results:
                        retrieve_span.set_attribute("retrieve.results", json.dumps(displayed_results))

                # Return search response
                return VectorStoreSearchResponse(
                    search_query=search_request.query,
                    data=data,
                    has_more=len(data) >= search_request.max_num_results
                )

        except (asyncpg.PostgresError, socket.gaierror) as e:
            SystemLogger.error(f"Postgres connection failed while searching vector store {vector_store_id}: {e}", exc_info=True)
            raise PostgresConnectionException()
        except AppBaseException:
            raise
        except Exception as e:
            SystemLogger.error(f"Error searching vector store {vector_store_id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error searching vector store: {str(e)}"
            )
