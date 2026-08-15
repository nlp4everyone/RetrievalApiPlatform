from typing import Any, Optional
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
import asyncpg, socket, json

# Schemas
from app.schemas.vector_store import *
from app.schemas.vector_store.requests import *
from app.schemas.vector_store.responses import *
from app.schemas.file.types import UploadingStatus
from app.schemas.vector_store.types import SearchType

# DB
from app.db.postgres import PostgresVectorStore
from app.db.vector_store import VectorStoreFactory
from app.startup import (get_dense_embedding,
                         get_postgres_pool,
                         get_sparse_embedding,
                         is_sparse_embedding_enabled)

# Helper
from app.utils.key_generator import generate_vectorstore_id
from app.utils.vector_store.utils import (convert_retrieved_chunks_to_search_results,
                                          normalize_search_filter,
                                          validate_vector_store_prefix)
from app.utils.datetime_utils import convert_to_unix_timestamp

# Exceptions
from app.exceptions import AppBaseException
from app.exceptions.postgres import PostgresConnectionException
from app.exceptions.vector_store import (UnsupportedMultipleFilesException,
                                         UnsupportedSearchTypeException)

# Logger
from loggers import SystemLogger

# Request context
from app.core.request_context import request_id_ctx

# TaskIQ worker
from app.tasks import ingest_vector_store_files

# Retrieval pipeline
from app.pipelines.retrieval import (RetrievalContext,
                                     build_retrieval_pipeline,
                                     hybrid_unavailable_reason,
                                     resolve_search_type)

# Tracing
from app.core.tracing import (OBSERVATION_TYPE,
                              ObservationType,
                              TRACE_INPUT,
                              TRACE_TAGS,
                              TRACE_USER_ID,
                              inject_trace_context,
                              observation_metadata,
                              set_span_attributes,
                              traced_span,
                              trace_metadata)


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
            UnsupportedMultipleFilesException: If more than one file_id is given
            PostgresConnectionException: If database connection fails
        """
        # Single-file ingestion only; reject upfront instead of polling into a store that never finishes.
        if request.file_ids is not None and len(request.file_ids) > 1:
            raise UnsupportedMultipleFilesException(num_files = len(request.file_ids))

        # Get current time in UTC for timestamps
        current_time = datetime.now(timezone.utc)
        created_at = current_time

        # Generate unique vector store ID
        vectorstore_id = generate_vectorstore_id()

        # Get PostgreSQL connection pool
        postgres_pool = get_postgres_pool()

        # Backend new vector stores are created on
        provider = VectorStoreFactory.default_provider()

        try:
            # Root span of the whole ingestion trace. Ingestion itself runs in the
            # TaskIQ worker, which joins this trace via the injected trace context
            # below - so the trace-level attributes have to be set here, on the
            # root span, not in the worker where Langfuse would ignore them.
            with traced_span(name="POST /v1/vector_stores",
                             attributes={TRACE_USER_ID: api_key,
                                         TRACE_TAGS: ["vector_store_ingestion"],
                                         TRACE_INPUT: json.dumps({
                                             "name": request.name,
                                             "file_ids": request.file_ids}),
                                         OBSERVATION_TYPE: ObservationType.SPAN,
                                         **trace_metadata(vector_store_id=vectorstore_id,
                                                          request_id=request_id_ctx.get(),
                                                          vector_store_type=str(provider))}) as trace_span:
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
                with traced_span(name="create_record",
                                 attributes={OBSERVATION_TYPE: ObservationType.SPAN}):
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
                        vector_store_type=provider
                    )

                # Determine chunking strategy parameters. Only static carries its
                # own sizing; every other case - the field omitted, or an explicit
                # {"type": "auto"} - is auto with the documented defaults. Kept as
                # one condition so the two auto spellings cannot drift apart.
                if (request.chunking_strategy is not None
                        and request.chunking_strategy.type == "static"):
                    chunking_strategy = "static"
                    chunk_size = request.chunking_strategy.static.max_chunk_size_tokens
                    chunk_overlap = request.chunking_strategy.static.chunk_overlap_tokens
                else:
                    chunking_strategy = "auto"
                    chunk_size = 800
                    chunk_overlap = 400

                # Start background processing of files using TaskIQ worker
                with traced_span(name="enqueue_ingestion",
                                 attributes={OBSERVATION_TYPE: ObservationType.SPAN,
                                             **observation_metadata(chunking_strategy=chunking_strategy,
                                                                    chunk_size=chunk_size,
                                                                    num_files=nums_in_progress_file)}):
                    await ingest_vector_store_files.kiq(
                        vectorstore_id=vectorstore_id,
                        api_key=api_key,
                        file_ids=request.file_ids,
                        chunking_strategy=chunking_strategy,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        request_id=request_id_ctx.get(),
                        # Carries this trace into the worker process
                        trace_context=inject_trace_context(),
                        vector_store_type=str(provider)
                    )

                SystemLogger.info(f"Vector store created: {vectorstore_id} ({nums_in_progress_file} file(s) queued)")

                set_span_attributes(trace_span, {
                    "vector_store.id": vectorstore_id,
                    "vector_store.type": str(provider),
                    "vector_store.num_files_queued": nums_in_progress_file,
                })

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

        # Read the record (raises if missing) - its vector_store_type tells us
        # which backend actually holds the collection, which may differ from the
        # currently configured provider
        record = await PostgresVectorStore.get_by_id(
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

            # Delete the actual vector collection from its backend
            vector_store = VectorStoreFactory.get_store(
                collection_name=vector_store_id,
                provider=record.get("vector_store_type")
            )
            await vector_store.delete_collection()

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
                     api_key: str,
                     search_type: Optional[SearchType] = None) -> VectorStoreSearchResponse:
        """
        Search a vector store for relevant chunks based on a query.

        This method handles the complete search workflow including tracing,
        embedding generation, vector store retrieval, and result formatting.

        Args:
            vector_store_id: ID of the vector store to search in
            search_request: Search request with query, filters, max_num_results, ranking_options
            api_key: API key for ownership validation
            search_type: How the query is answered, for internal callers that
                need to pin it. Takes precedence over the request's search_type
                field. Left unset by both, it is resolved per search: hybrid
                when sparse embedding is enabled *and* this collection carries
                sparse vectors, dense otherwise

        Returns:
            VectorStoreSearchResponse containing search results

        Raises:
            PostgresConnectionException: If database connection fails
            UnsupportedSearchTypeException: If a search type was pinned that
                this vector store cannot answer
            HTTPException: If search operation fails
        """
        # Validate vector store id
        validate_vector_store_prefix(vector_store_id)

        postgres_pool = get_postgres_pool()

        # Read the record (raises if missing) - vector_store_type says which
        # backend holds the collection
        record = await PostgresVectorStore.get_by_id(
            pool=postgres_pool,
            vector_store_id=vector_store_id,
            api_key=api_key
        )
        provider = record.get("vector_store_type")

        # Translate the request filter into the backend-neutral form; each
        # backend renders it into its own filter language
        neutral_filter = normalize_search_filter(search_request.filters)

        # NOTE: only the first query is used when a list is given.
        query = (search_request.query if isinstance(search_request.query, str)
                 else search_request.query[0])

        # Only score_threshold is honoured so far; ranker and rewrite_query
        # still have no effect. 0.0 is the schema default and means "keep
        # everything", so it is not passed down as a threshold - the backends
        # take None for "no filtering".
        score_threshold = None
        if search_request.ranking_options and search_request.ranking_options.score_threshold > 0:
            score_threshold = search_request.ranking_options.score_threshold

        vector_store = VectorStoreFactory.get_store(collection_name=vector_store_id,
                                                    provider=provider)

        # An explicit argument beats the request field, so an internal caller
        # can pin a search type regardless of what the client asked for
        if search_type is None:
            search_type = search_request.requested_search_type

        # Resolved before the span opens so the trace records the search that
        # actually ran, not the one that was requested
        if search_type is None:
            search_type = await resolve_search_type(vector_store)
        elif search_type == SearchType.HYBRID:
            # Pinned hybrid is checked rather than attempted: without this the
            # request dies as a ValueError out of build_retrieval_pipeline, or
            # as a backend error on a collection with no sparse field - both
            # surface as a 500 for what is a bad request
            reason = await hybrid_unavailable_reason(vector_store)
            if reason is not None:
                raise UnsupportedSearchTypeException(search_type=str(search_type),
                                                     reason=reason)

        try:
            # Root span of the search trace. The pipeline runs inside it and
            # emits one observation per stage, so the trace-level attributes
            # belong here, on the root span, where the api_key is known.
            with traced_span(name=f"POST /v1/vector_stores/{vector_store_id}/search",
                             attributes={TRACE_USER_ID: api_key,
                                         TRACE_TAGS: ["vector_store_search"],
                                         TRACE_INPUT: json.dumps({
                                             "query": search_request.query,
                                             "max_num_results": search_request.max_num_results}),
                                         OBSERVATION_TYPE: ObservationType.SPAN,
                                         **trace_metadata(vector_store_id=vector_store_id,
                                                          vector_store_type=str(provider),
                                                          search_type=str(search_type))}) as search_span:
                set_span_attributes(search_span, {"vector_store.id": vector_store_id,
                                                  "search.type": str(search_type)})

                pipeline = build_retrieval_pipeline(vector_store=vector_store,
                                                    embed_fn=get_dense_embedding,
                                                    search_type=search_type,
                                                    sparse_embed_fn=(get_sparse_embedding
                                                                     if is_sparse_embedding_enabled() else None))

                context = RetrievalContext(vector_store_id=vector_store_id,
                                           api_key=api_key,
                                           query=query,
                                           limit=search_request.max_num_results,
                                           filters=neutral_filter,
                                           score_threshold=score_threshold)
                await pipeline.run(context)

                # Convert results to API response format
                data = convert_retrieved_chunks_to_search_results(context.results)

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
