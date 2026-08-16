# Detailed processing flow

> This document describes exactly what happens at each step of the pipeline — function names, variable names, and each gate's logic. For developers who need a deep understanding or are debugging the system.
> Scope: file creation (upload), vector store creation (async ingestion), and search.

## Component diagram

```text
App Startup  (FastAPI lifespan, above the yield in app/app.py → app/startup.py)
        ├── init_tracing()                OpenTelemetry TracerProvider → export OTLP to Langfuse
        ├── init_embed_model()             EmbeddingService.from_settings() → check_connection()
        │                                  ↳ check_connection() embeds a throwaway string and CACHES
        │                                    the vector dimension on embed_model.dimension
        │                                    (read later via get_dense_embedding_dim())
        ├── init_sparse_embed_model()      opt-in mirror of the above, gated on SPARSE_EMBEDDING_ENABLED
        │                                  false → logs, leaves sparse_embed_model = None, boots dense-only
        │                                  true  → SparseEmbeddingService.from_settings() → check_connection()
        │                                          an unreachable sparse server fails the BOOT, not the
        │                                          first search — same contract as the dense one
        │                                  ↳ callers branch on is_sparse_embedding_enabled(); calling
        │                                    get_sparse_embed_model() while off raises RuntimeError
        ├── init_postgres() + wait_for_postgres()   create pool, retry 5x / 0.5s, then _create_table()
        ├── init_vector_store()            one connection per backend whose credentials are filled
        │                                  in → check_connection() → register_connection(provider, conn)
        │                                  VECTOR_STORE_PROVIDER is always in that set, and it being
        │                                  unreachable fails the BOOT — new stores are created on it,
        │                                  so a process that cannot reach it has nothing to offer;
        │                                  any OTHER backend is skipped with a WARNING instead, since
        │                                  a leftover .env block is enough to have it connected at all
        ├── init_minio()                   MinioService, create UPLOADED_FILE_BUCKET if missing
        └── init_io_executor()             ThreadPoolExecutor(IO_THREAD_POOL_SIZE=32, prefix "io")
                                           the web process needs the I/O pool only — it uploads to
                                           and deletes from MinIO, but never parses or chunks

Worker Startup  (TaskIQ WORKER_STARTUP, app/tasks/broker.py::_initialize_services)
        ├── init_tracing() · init_postgres() · init_minio()
        ├── init_io_executor()             same I/O pool, here used for MinIO downloads
        ├── init_cpu_executor()            ThreadPoolExecutor(CPU_THREAD_POOL_SIZE=4, prefix "cpu")
        │                                  KEPT SEPARATE from the I/O pool: chunking is CPU-bound and
        │                                  must not queue behind - or be starved by - a slow transfer
        ├── init_download_semaphore()      asyncio.Semaphore(DOWNLOAD_CONCURRENCY=4)
        │                                  caps concurrent downloads so one burst of ingestion jobs
        │                                  cannot exhaust the I/O pool on its own
        ├── init_vector_store() · init_embed_model() · init_sparse_embed_model()
        │                                  the worker embeds too, so it builds the same two services
        │                                  the web process does — sparse still a no-op when disabled
        │                                  ⓘ init_postgres() here only creates the pool: wait_for_postgres()
        │                                    and _create_table() run on the web process alone
        └── init_parsing_service()         ParsingService.from_settings() ← worker-only
                                           the whole sequence runs once, guarded by _initialized
```

Note that `init_embed_model()` (and `init_sparse_embed_model()` when `SPARSE_EMBEDDING_ENABLED`) probes a server *outside* this stack, so the embedding server must already be up or **both processes fail to boot** — deliberately, since an unreachable model server would otherwise surface as a failed ingestion much later. Start [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) (`make up dense`, or `make up hybrid` for sparse too) before `make up` here; see [Embedding Server](README.md#embedding-server).

```text
Client  (OpenAI SDK / HTTP client)
        │  multipart/form-data (upload) or JSON
        ▼
FastAPI HTTP Gateway
        ├── RequestIDMiddleware      assign/reuse X-Request-Id → request_id_ctx (ContextVar) → echoed back in response header
        ├── file_router              POST /v1/files
        └── vector_store_router      POST /v1/vector_stores, POST /v1/vector_stores/{id}/search

① POST /v1/files   (multipart: purpose, file, expires_after)
        │
        ├── validate_file  (app/api/dependencies.py, runs BEFORE the handler)
        │       validate_file_type()   content_type ∉ ALLOWED_MIME_TYPES, ext ∉ ALLOWED_EXTENSIONS,
        │                              or MIME_TYPE_MAPPING[content_type] != ext  → 415
        │       validate_file_size()   (file.size / 1MB) > MAX_FILE_SIZE (100)?  → FileSizeLimitExceededException (413)
        ▼
    FileService.upload_file(file, api_key, purpose, expires_after_seconds)
        ├── generate_file_id()            → "file-{8 hex}"
        ├── object_path = "{api_key}/uploads/{uuid4().hex}_{filename}"
        ├── MinioFileStore.upload_file(minio_client, file.file, file_size, object_path,
        │                              UPLOADED_FILE_BUCKET, content_type, executor=get_io_executor())
        │       put_object runs on the dedicated I/O pool, not the loop's default executor
        │       error → log + re-raise (nothing to clean up yet at this step)
        └── PostgresFileStore.insert_file(id=file_id, api_key, bytes, purpose, created_at, expires_at,
                                          metadata={filename, minio_bucket, minio_path, etag})
                connection error (asyncpg.PostgresError | socket.gaierror)?
                    → raise PostgresConnectionException (503)
                    → the object already uploaded to MinIO is left behind, becoming an ORPHAN — only logged,
                      NO automatic cleanup mechanism (no compensating transaction/rollback)
                success → returns FileObject (created_at/expires_at as Unix timestamps)

② POST /v1/vector_stores   {name, file_ids, chunking_strategy} → async ingestion
        ▼
    VectorStoreService.create(request, api_key)
        ├── len(request.file_ids) > 1 ?  → UnsupportedMultipleFilesException (400)
        │       ⓘ single-file ingestion only, rejected upfront rather than letting the caller
        │         poll a store that would never finish
        ├── generate_vectorstore_id()       → "vs_{32 hex}"
        ├── provider = VectorStoreFactory.default_provider()      ← from VECTOR_STORE_PROVIDER
        │
        ├── traced_span("POST /v1/vector_stores")   ROOT span of the ingestion trace
        │       trace attributes (user_id, tags, input, metadata) MUST be set here, not in the
        │       worker — the worker joins this trace and Langfuse ignores trace-level attributes
        │       set on a non-root span
        │
        │       ├── traced_span("create_record")
        │       │       PostgresVectorStore.create(status=IN_PROGRESS, usage_bytes=0,
        │       │                                  vector_store_type=provider, ...)
        │       │
        │       ├── Resolve chunking_strategy + chunk_size/chunk_overlap:
        │       │       request.chunking_strategy.type == "static"  → "static" (from request.static)
        │       │       anything else — field omitted, or an explicit {"type": "auto"} → "auto" (800 / 400)
        │       │
        │       └── traced_span("enqueue_ingestion")
        │               ingest_vector_store_files.kiq(vectorstore_id, api_key, file_ids,
        │                                             chunking_strategy, chunk_size, chunk_overlap,
        │                                             request_id=request_id_ctx.get(),
        │                                             trace_context=inject_trace_context(),   ← W3C traceparent
        │                                             vector_store_type=str(provider))
        │                                                    ↳ onto the Redis stream broker (TaskIQ)
        │
        └── return VectorStoreObject(status="in_progress")   ← returned immediately, does NOT wait for ingestion

        Note: runs on the separate taskiq_worker process, outside the HTTP request/response lifecycle
        ▼
    app/tasks/ingestion_task.py::ingest_vector_store_files(...)
        │  request_id_ctx.set(request_id)   ← re-bind the ContextVar inside the worker process
        │  delegate to IngestionService, log + re-raise on failure, reset the contextvar
        ▼
    IngestionService.ingest_vector_store_files(...)
        │
        ├──▶ Step 0: chunking_strategy in ("auto","static") ?
        │       NO  → log error → _mark_failed(status=FAILED) → raise ValueError
        │              (producer and worker have drifted apart; failing beats silently no-oping)
        │
        ├──▶ Step 1: PostgresFileStore.check_existing_files(file_ids) → existing_file_ids
        │       file_ids given but none exist? → log error → _mark_failed → raise ValueError
        │       no file_ids at all → usage_bytes stays 0, skip Steps 2-3, jump to Step 4
        │                            (a store created without files is legitimately empty)
        │       else → get_total_bytes(existing_file_ids) → usage_bytes
        │              get_metadata_for_files(existing_file_ids) → files_metadata
        │
        ├──▶ Step 2: len(files_metadata) == 1 ?   ← second line of defense behind the API-level check
        │       NO (0 or >1) → log error → _mark_failed(status=FAILED) → raise ValueError
        │              0 survives Step 1: get_metadata_for_files drops rows whose metadata
        │              is NULL, so a file that exists without metadata lands here
        │
        ├──▶ Step 3: _ingest_single_file(...) → num_inserted
        │               vector_store = VectorStoreFactory.get_store(collection_name=vectorstore_id,
        │                                                           provider=vector_store_type)
        │               pipeline = build_ingestion_pipeline(minio_client, vector_store, embed_fn=get_dense_embedding,
        │                                                   parsing_service, chunking_strategy, chunk_size, chunk_overlap,
        │                                                   sparse_embed_fn=get_sparse_embedding
        │                                                                   if is_sparse_embedding_enabled() else None)
        │                                                   ↳ the worker hands over a sparse embedder only when it
        │                                                     actually has one; supplying it is what makes the
        │                                                     collection hybrid (dense + sparse on the same point)
        │               context  = IngestionContext(vector_store_id, api_key, file_id, file_metadata, ...)
        │               await pipeline.run(context, parent_carrier=trace_context)
        │                                                   ↳ nests every stage inside the HTTP request's trace
        │
        │               ┌── IngestionPipeline stages (one Langfuse observation each) ────────────┐
        │               │                                                                        │
        │               │  download   MinioFileStore.download_file → context.raw_bytes           │
        │               │             bucket/path read from context.file_metadata                │
        │               │             async with get_download_semaphore()                        │
        │               │                 ← bounded by DOWNLOAD_CONCURRENCY=4                    │
        │               │             _fetch_object() (get_object + .read() + close) runs as ONE │
        │               │             unit on get_io_executor(): opening the stream is just      │
        │               │             headers, the real transfer is .read() — both must leave    │
        │               │             the event loop together                                    │
        │               │             returns None → ValueError("Failed to download file: ...")  │
        │               │                                                                        │
        │               │  parse      ParsingService.parse(raw_bytes, context.file_extension)    │
        │               │             → context.text  (Markdown for EVERY format)                │
        │               │             .pdf → PDF_PARSER_PROVIDER (LlamaParseProvider)            │
        │               │             .txt .md .docx .doc .png .jpg .jpeg → UnstructuredProvider │
        │               │                 partition_via_api on asyncio.to_thread, then the JSON  │
        │               │                 element list is rendered to Markdown locally           │
        │               │             unmapped extension → ValueError("Unsupported file format") │
        │               │                                                                        │
        │               │  chunk      ChunkingService.split_text(text) → context.chunks          │
        │               │             provider from CHUNKING_PROVIDER, size/overlap per request  │
        │               │             runs on get_cpu_executor() — CPU-bound, kept off the I/O   │
        │               │             pool                                                       │
        │               │                                                                        │
        │               │  embed_index  ONE streaming stage (EmbedAndIndexStage)                 │
        │               │             ① embedding_dim = await get_dense_embedding_dim()          │
        │               │                (dimension cached at startup — NOT inferred from the    │
        │               │                 first embedding result; that is what makes streaming   │
        │               │                 possible)                                              │
        │               │             ② ensure_collection(embedding_dim, with_sparse=…) ONCE     │
        │               │                with_sparse = a sparse_embed_fn was supplied            │
        │               │                → context.metrics["collection_created"]                 │
        │               │             ②ᵇ use_sparse = want_sparse and await supports_sparse()    │
        │               │                the COLLECTION is asked, not the config: an existing    │
        │               │                collection made before sparse was switched on has no    │
        │               │                sparse field and NEITHER backend can add one, and       │
        │               │                upserting a sparse vector it cannot hold fails every    │
        │               │                batch                                                   │
        │               │                ⓘ ensure_collection returning False because another     │
        │               │                  worker won the race is not an error on either         │
        │               │                  backend — it re-checks existence and carries on       │
        │               │                → context.metrics["sparse_enabled"]                     │
        │               │             ③ chunks → batches of EMBEDDING_UPLOAD_BATCH_SIZE (16),    │
        │               │                asyncio.gather under Semaphore(BATCH_CONCURRENCY=4);    │
        │               │                per batch: embed → build Documents → insert_documents,  │
        │               │                ALL while holding the same semaphore slot               │
        │               │                use_sparse → dense and sparse embedded together in one  │
        │               │                  asyncio.gather, so the batch costs the SLOWER server  │
        │               │                  rather than both in turn, and both vectors ride on    │
        │               │                  the same point (no second pass over the file)         │
        │               │                → peak memory ≈ batch_size × concurrency, independent   │
        │               │                  of file size; writes start with the first batch       │
        │               │                  instead of after the whole file is embedded           │
        │               │             ④ context.num_inserted = sum(...)                          │
        │               │                embed_wall_clock_s / index_wall_clock_s come from       │
        │               │                _union_duration(), merging overlapping intervals so     │
        │               │                concurrent batches are not double-counted               │
        │               │             ⓘ reports as ObservationType.SPAN (the default), not       │
        │               │               EMBEDDING as the pre-merge EmbedStage did                │
        │               │                                                                        │
        │               └────────────────────────────────────────────────────────────────────────┘
        │
        │               any stage raises? → remaining stages do not run → the span records the error
        │                                 → IngestionService catches → _mark_failed(status=FAILED) → re-raise
        │
        │               num_inserted == 0? → the file yielded no chunks, so the collection is empty
        │                                  → _mark_failed(status=FAILED) → raise ValueError
        │
        └──▶ Step 4: PostgresVectorStore.update(status=COMPLETED, usage_bytes, last_active_at=now())
                     ← reached only when the collection is actually searchable, or when the
                       store was created with no file_ids at all

③ POST /v1/vector_stores/{id}/search   {query, max_num_results, filters, ranking_options, search_type}
        ▼
    VectorStoreService.search(vector_store_id, search_request, api_key, search_type=None)
        ├── validate_vector_store_prefix(id)    no "vs" prefix?  → WrongPrefixVectorstoreException
        ├── PostgresVectorStore.get_by_id(id, api_key)   not found? → VectorStoreNotFoundException
        │       provider = record["vector_store_type"]   ← the backend this collection actually lives on
        │
        ├── neutral_filter = normalize_search_filter(search_request.filters)
        │       ComparisonFilter → FieldCondition(key, FilterOperator, value)
        │       CompoundFilter   → FilterGroup(FilterCombinator, [...])
        │       each backend renders this tree itself (to_qdrant_filter / to_milvus_expression)
        │
        │  query = search_request.query if str, else search_request.query[0]
        │      ⚠ if query is a List[str], ONLY the first element is used — the rest are dropped
        │
        ├── score_threshold = ranking_options.score_threshold  (only when > 0, else None)
        │       0.0 is the schema default and means "keep everything", so it is NOT passed down —
        │       the backends read None as "no filtering"
        │       ⓘ of ranking_options, only score_threshold reaches the backend; ranker and
        │         rewrite_query are still accepted and ignored
        │
        ├── vector_store = VectorStoreFactory.get_store(collection_name=id, provider=provider)
        │       the provider comes from the ROW, never from config: a store created on a backend
        │       this deployment no longer connects raises RuntimeError naming the fix
        │       ("fill in its connection settings") instead of querying the wrong engine
        │       Milvus only: the physical collection is the id itself (vs_{hex} is already legal
        │       there), except for legacy hyphenated ids which stay folded (vs-a1b2… → vs_a1b2…);
        │       a search that finds it unloaded — as after a server restart — loads it and
        │       retries ONCE rather than returning empty
        │
        ├── which search actually runs — resolved BEFORE the span opens, so the trace records
        │   what ran rather than what was asked for:
        │       1. the search_type ARGUMENT (internal callers only) wins outright
        │       2. else search_request.requested_search_type — the request's "auto"|"dense"|"hybrid"
        │             field, mapped to None|DENSE|HYBRID ("auto" is the default)
        │             ⓘ not part of the OpenAI schema; the stock SDK sends it through
        │               extra_body={"search_type": "hybrid"}
        │       3. still None ("auto") → resolve_search_type(vector_store)
        │             hybrid_unavailable_reason(vector_store) is None → HYBRID, otherwise DENSE
        │       4. pinned HYBRID → hybrid_unavailable_reason(vector_store) is re-checked, and a
        │             reason means UnsupportedSearchTypeException (400) instead of a silent
        │             fallback to dense — a caller who named hybrid and got dense results would
        │             be measuring retrieval quality on a config they don't think they're running
        │                 "sparse embedding is not enabled on this server"            (server-side)
        │                 "this vector store holds no sparse vectors — ingested before …" (store-side)
        │             two reasons because they need different fixes: enable sparse, vs re-ingest
        │       (pinned DENSE always runs: a hybrid-capable store can still be searched dense)
        │
        ├── traced_span("POST /v1/vector_stores/{id}/search")   ROOT span
        │       trace attributes: user_id=api_key, tags=["vector_store_search"],
        │                         input={query, max_num_results}, metadata={vector_store_id,
        │                         vector_store_type, search_type}
        │
        │       pipeline     = build_retrieval_pipeline(vector_store, embed_fn=get_dense_embedding,
        │                                               search_type, sparse_embed_fn)
        │              └── _build_plan(search_type, vector_store):
        │                      SearchType.DENSE  → [DenseRetriever],  PassthroughFusion
        │                      SearchType.HYBRID → [HybridRetriever], PassthroughFusion
        │                      anything else     → ValueError("Unsupported search type")
        │                  HYBRID without a sparse_embed_fn → ValueError
        │                      (unreachable from the API: step 4 above rejects it as a 400 first)
        │       context      = RetrievalContext(vector_store_id, api_key, query, limit=max_num_results,
        │                                       filters=neutral_filter, score_threshold=score_threshold)
        │       await pipeline.run(context)      ← no parent_carrier: already inside this request's span
        │
        │       ┌── RetrievalPipeline stages (one Langfuse observation each) ──────────────────┐
        │       │                                                                              │
        │       │  embed_query   embed_fn([query]) → context.dense_vector                      │
        │       │                HYBRID: + sparse_embed_fn([query]) → context.sparse_vector    │
        │       │                    (both awaited together — costs the slower server)         │
        │       │                                            [ObservationType.EMBEDDING]       │
        │       │                                                                              │
        │       │  retrieve      every retriever runs concurrently (asyncio.gather), each      │
        │       │                handed the same RetrievalQuery (text, limit, both vectors,    │
        │       │                filters, score_threshold)                                     │
        │       │                → context.candidates = {retriever.name: [RetrievedChunk]}     │
        │       │                Dense/HybridRetriever: collection_exists()? NO → []           │
        │       │                    (store row can exist before ingestion created the         │
        │       │                     collection — empty result, not an error)                 │
        │       │                HybridRetriever: one retrieve() carrying both vectors;        │
        │       │                    each branch is prefetched limit×N deep, so a doc just     │
        │       │                    outside the dense top-k can still win on rank, and the    │
        │       │                    backend fuses server-side → ONE list                      │
        │       │                        Qdrant: prefetch + FusionQuery(RRF), or RrfQuery(k)   │
        │       │                                when retrieval.rrf_k is set                   │
        │       │                        Milvus: hybrid_search(AnnSearchRequest×2, RRFRanker)  │
        │       │                    score_threshold rides on the DENSE branch only — it is    │
        │       │                    a similarity score, and against RRF output (~1/(60+rank)) │
        │       │                    it would drop everything (Milvus takes it as a `radius`   │
        │       │                    search param rather than a score floor)                   │
        │       │                    no sparse vector on the query? → degrades to dense rather │
        │       │                    than failing; the span says retrieval.hybrid.used_sparse  │
        │       │                                            [ObservationType.RETRIEVER]       │
        │       │                                                                              │
        │       │  fuse          fusion.fuse(candidates, limit) → context.results              │
        │       │                PassthroughFusion: >1 candidate list → ValueError             │
        │       │                    (hybrid still yields one list — the backend fused it)     │
        │       │                emits_span() == False when there is nothing to fuse           │
        │       │                                                                              │
        │       └──────────────────────────────────────────────────────────────────────────────┘
        │
        │       data = convert_retrieved_chunks_to_search_results(context.results)
        │              content whitespace-normalised, score rounded to 5 decimals HERE and only
        │              here — the threshold above and the RRF ranking both used the full score
        │
        └── return VectorStoreSearchResponse(search_query, data, has_more = len(data) >= max_num_results)

App Shutdown  (FastAPI lifespan, below the yield in app/app.py)
        ├── get_io_executor().shutdown(cancel_futures=True)   drop the I/O threads first, so nothing
        │                                        new reaches MinIO or the clients closed below
        ├── VectorStoreFactory.close_all()       closes every registered vector store connection
        └── get_postgres_client().close()        closes the asyncpg pool
                                                 the TracerProvider is deliberately left alone: it
                                                 registers its own atexit flush, and shutting it down
                                                 here would cut these shutdown logs short

Worker Shutdown  (TaskIQ WORKER_SHUTDOWN, app/tasks/broker.py)
        ├── get_postgres_client().close()        closes the asyncpg pool — only if _initialized
        └── VectorStoreFactory.close_all()       closes every registered vector store connection
                                                 (runs unconditionally, even on a failed startup)
```

## Where the tracing comes from

Stages never open spans. `Pipeline.run()` does, and it is the only thing that does:

```text
traced_span(pipeline.name, root_attributes(context), parent_carrier)
    for stage in stages:
        traced = stage.emits_span(context)          ← decided BEFORE run(), can inspect prior output
        with traced_span(stage.name, {OBSERVATION_TYPE: stage.observation_type}) if traced else nullcontext():
            await stage.run(context)
            set_span_attributes(span, stage.span_attributes(context))   ← read AFTER run() succeeded
    set_span_attributes(pipeline_span, result_attributes(context))
```

A stage reporting `emits_span() == False` still runs — it just stays out of the trace, so a step that was a no-op on this particular run adds no noise. This is why the trace shape stays correct when stages are added, removed, or reordered.
