# Detailed processing flow

> This document describes exactly what happens at each step of the pipeline — function names, variable names, and each gate's logic. For developers who need a deep understanding or are debugging the system.
> Scope: file creation (upload), vector store creation (async ingest), and search.

## Component diagram

```text
App Startup  (FastAPI "startup" event, app/app.py)
        ├── init_tracing()                OpenTelemetry TracerProvider → export OTLP to Langfuse
        ├── init_embed_model()             AsyncOpenAI client → vLLM dense embedding, test call embeddings.create(["Hello"])
        ├── init_postgres() + wait_for_postgres()   create pool, retry 5x / 0.5s, then _create_table()
        ├── init_qdrant()                  QdrantService._check_connection()
        └── init_minio()                   MinioService, create UPLOADED_FILE_BUCKET if missing

Client  (OpenAI SDK / HTTP client)
        │  multipart/form-data (upload) or JSON
        ▼
FastAPI HTTP Gateway
        ├── RequestIDMiddleware      assign/reuse X-Request-Id → request_id_ctx (ContextVar) → echoed back in response header
        ├── file_router              POST /v1/files
        └── vector_store_router      POST /v1/vector_stores, POST /v1/vector_stores/{id}/search

① POST /v1/files   (multipart: purpose, file, expires_after)
        │
        ├── validate_file  (FastAPI dependency, runs BEFORE the handler)
        │       validate_file_type()   content_type ∉ ALLOWED_MIME_TYPES, ext ∉ ALLOWED_EXTENSIONS,
        │                              or MIME_TYPE_MAPPING[content_type] != ext  → 415
        │       validate_file_size()   (file.size / 1MB) > MAX_FILE_SIZE (100)?  → FileSizeLimitExceededException (413)
        ▼
    FileService.upload_file(file, api_key, purpose, expires_after_seconds)
        ├── generate_file_id()            → "file-{8 hex}"
        ├── object_path = "{api_key}/uploads/{uuid4().hex}_{filename}"
        ├── MinioFileStore.upload_file(minio_client, file.file, file_size, object_path, UPLOADED_FILE_BUCKET, content_type)
        │       error → log + re-raise (nothing to clean up yet at this step)
        └── PostgresFileStore.insert_file(id=file_id, api_key, bytes, purpose, created_at, expires_at,
                                          metadata={filename, minio_bucket, minio_path, etag})
                connection error (asyncpg.PostgresError | socket.gaierror)?
                    → raise PostgresConnectionException (503)
                    → the object already uploaded to MinIO is left behind, becoming an ORPHAN — only logged,
                      NO automatic cleanup mechanism (no compensating transaction/rollback)
                success → returns FileObject (created_at/expires_at as Unix timestamps)

② POST /v1/vector_stores   {name, file_ids, chunking_strategy} → async ingest
        ▼
    VectorStoreService.create(request, api_key)
        ├── generate_vectorstore_id()       → "vs-{32 hex}"
        ├── PostgresVectorStore.create(status=UploadingStatus.IN_PROGRESS, usage_bytes=0, ...)
        ├── Resolve chunking_strategy + chunk_size/chunk_overlap:
        │       request.chunking_strategy is None                     → "auto"   (chunk_size=800, chunk_overlap=400)
        │       request.chunking_strategy.type == "static"             → "static" (chunk_size/overlap from request.static)
        │       request.chunking_strategy.type == "auto" (sent explicitly) → falls into else → "fuse" (chunk_size=800, overlap=400)
        │           ⚠ "fuse" is not in ("auto","static") so the Worker will SKIP the embed/upsert step —
        │             the file still gets parsed + chunked but NEVER makes it into Qdrant, even though status still reports "completed".
        │             Only happens when the client explicitly sends {"type": "auto"} instead of omitting chunking_strategy.
        ├── process_vector_store_files.kiq(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap,
        │                                  request_id=request_id_ctx.get())   ← enqueued onto the Redis stream broker (TaskIQ)
        └── return VectorStoreObject(status="in_progress")   ← response returned immediately, does NOT wait for ingest to finish

        Note: runs on the separate taskiq_worker.py process, outside the HTTP request/response lifecycle
        ▼
    process_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id)
        │  request_id_ctx.set(request_id)   ← re-bind the ContextVar inside the worker process (see Request correlation)
        │
        ├──▶ Step 1: PostgresFileStore.check_existing_files(file_ids) → existing_file_ids
        │       existing_file_ids empty?  → usage_bytes stays 0, skip Steps 2-3, jump straight to Step 4 (status=COMPLETED)
        │       otherwise → get_total_bytes(file_ids) → usage_bytes ; get_metadata_for_files(existing_file_ids) → files_metadata
        │
        ├──▶ Step 2: len(files_metadata) == 1 ?   ← only supports exactly ONE file (TODO: multi-file processing)
        │       NO (0 or >1)  → chunked_texts = []  (extra/multiple files are silently skipped, no warning logged)
        │       YES → load_and_chunk_file(minio_client, files_metadata[0], chunk_size)
        │               ParserFactory.get(ext)    .txt/.md → AsyncTextParser | .pdf → LlamaParseParser
        │                   no parser for this ext?  → ValueError("Unsupported file format")
        │               MinioFileStore.download_file(bucket, path) → file_bytes (None → ValueError)
        │               parser.parse(file_bytes) → text
        │               ChonkieChunkingService(chunk_size).split_text(text) → chunked_texts
        │               error at any step? → _mark_failed(vectorstore_id, api_key, usage_bytes) [status=FAILED] → re-raise
        │
        ├──▶ Step 3: chunking_strategy in ["auto","static"] AND chunked_texts non-empty?
        │       NO  → embed/upsert is skipped entirely (see ⚠ "fuse" above)
        │       YES → embed_and_upload_chunks(qdrant_vector_store, chunked_texts, source_file_id=file_ids[0], vectorstore_id, api_key)
        │               split into batches of EMBEDDING_UPLOAD_BATCH_SIZE (16)
        │               traced_span("/v1/vector_stores")   ← Langfuse trace wrapping the whole ingest
        │               first batch runs ALONE first  ← creates the Qdrant collection before any race
        │                       get_dense_embedding(batch) → Document(page_content, metadata={"source": file_id})
        │                       → qdrant_vector_store.insert_documents(documents, embeddings, upload_batch_size)
        │               remaining batches run concurrently (asyncio.gather), capped by asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)
        │               error at any step? → _mark_failed(...) [status=FAILED] → re-raise
        │
        └──▶ Step 4: PostgresVectorStore.update(status=UploadingStatus.COMPLETED, usage_bytes, last_active_at=now())

③ POST /v1/vector_stores/{id}/search   {query, max_num_results, filters, ranking_options}
        ▼
    VectorStoreService.search(vector_store_id, search_request, api_key)
        ├── validate_vector_store_prefix(id)    no "vs" prefix?  → WrongPrefixVectorstoreException
        ├── PostgresVectorStore._check_vector_store_existence(id, api_key)   not found? → VectorStoreNotFoundException
        │
        │  qdrant_filter = None   ← TODO: search_request.filters is NOT applied (_normalize_qdrant_filter not implemented)
        │  search_params is built from ranking_options but is NOT passed into retrieve() below  ← has no effect yet
        │
        ├── traced_span("/v1/vector_stores/{id}/search")   root Langfuse trace (langfuse.trace.input={query, max_num_results})
        │       queries = [query] if str, else query[:1]
        │           ⚠ if query is a List[str], ONLY the first element is used — the rest are dropped
        │
        │       traced_span("embedding")
        │               get_dense_embedding(queries) → queries_vectors
        │               embed_span: embedding.num_queries, embedding.model, embedding.dims
        │
        │       traced_span("retrieve")
        │               qdrant_service.client.collection_exists(vector_store_id)?
        │                   NO  → data=[]  (e.g. still ingesting, or ingest failed before the collection was created — see ⚠ "fuse" in step ②)
        │                   YES → qdrant_vector_store.retrieve(query_vectors=queries_vectors, query_filter=qdrant_filter, limit=max_num_results)
        │                         convert_query_response_to_search_results(retrieved_results)
        │                               payload["page_content"] → non-empty lines joined with " " (strips extra whitespace/newlines)
        │                               payload["metadata"] → SearchResult.attributes
        │
        └── return VectorStoreSearchResponse(search_query, data, has_more = len(data) >= max_num_results)

        Note: `filters` and `ranking_options` are accepted by the schema but not yet applied to the Qdrant query
              (see [Design Decisions](DESIGN_DECISIONS.md)).

Worker Shutdown  (TaskIQ WORKER_SHUTDOWN event, taskiq_worker.py)
        ├── postgres_service.close()          closes the asyncpg pool
        └── qdrant_service.client.close()     sync or async, checked via inspect.iscoroutinefunction
```