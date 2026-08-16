# Detailed Components

## API layer (`app/api/`)

Everything that exists only because this app is served over HTTP. Nothing outside this package imports it, and `app.components`/`app.pipelines` never reach up here — which is what lets the TaskIQ worker run without FastAPI in its path.

### `router/file_router.py` — tag "File"

All endpoints require `Depends(verify_api_key)`.

| Method | Path | Handler | Notes |
|---|---|---|---|
| POST | `/v1/files` | `upload_file` | Form fields `purpose`, `file` (via `Depends(validate_file)`), `expires_after[anchor \| seconds]` |
| GET | `/v1/files` | `list_files` | Query via `FileQueryRequest` (extends `PaginationParams`) |
| GET | `/v1/files/{file_id}` | `get_file_by_id` | — |
| DELETE | `/v1/files/{file_id}` | `delete_file` | Returns `FileDeletedResponse` |

### `router/vector_store_router.py` — tag "Vector Stores"

| Method | Path | Handler | Notes |
|---|---|---|---|
| POST | `/v1/vector_stores` | `create_vector_store` | Body `VectorStoreCreateRequest`; enqueues the ingestion job. More than one `file_id` → 400 |
| GET | `/v1/vector_stores` | `list_vector_stores` | Query `VectorStoreQueryRequest` |
| GET | `/v1/vector_stores/{id}` | `get_vector_store` | — |
| POST | `/v1/vector_stores/{id}` | `modify_vector_store` | OpenAI-style: update uses POST, not PATCH |
| DELETE | `/v1/vector_stores/{id}` | `delete_vector_store` | — |
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters` and `search_type` applied, of `ranking_options` only `score_threshold` |

### `dependencies.py`

`validate_file_size` (rejects over `MAX_FILE_SIZE` MB), `validate_file_type` (checks MIME + extension + their consistency via `MIME_TYPE_MAPPING`), composed as `validate_file` — used as `Depends(validate_file)` on the upload endpoint.

### `security.py`

`verify_api_key` requires an `Authorization: Bearer <token>` header, compares the token against the single configured `FASTAPI_API_KEY`, and returns the token as `api_key` — which is then used to scope Postgres rows. Because there is exactly one valid token, this is effectively single-tenant auth even though the schema is shaped for multi-tenant ownership (see [Design Decisions](DESIGN_DECISIONS.md)).

### `middleware.py`

`RequestIDMiddleware` is a raw ASGI middleware (not `BaseHTTPMiddleware` — that would reset the contextvar before uvicorn's access logger fires). It reuses a client-supplied `X-Request-Id` header if present, else generates `req_{uuid4().hex}`, binds it to `request_id_ctx` (a `contextvars.ContextVar` in `app/core/request_context.py`) for the request's duration, and echoes it back on the response. The same ID is passed into `ingest_vector_store_files.kiq(...)` and re-bound inside the worker task, so a single request ID threads through HTTP logs and the async ingestion job that request triggered.

`app/app.py` also defines `GET /health` (excluded from OpenAPI schema), registers `RequestIDMiddleware`, the global `AppBaseException` handler, and a `lifespan` that brings up tracing → embed model → Postgres (pool + `wait_for_postgres` + table creation) → vector store → MinIO → I/O pool, in that order, then releases the I/O pool, the vector store connections and the Postgres pool below its `yield` — one function, so a client opened above it cannot be forgotten below. It also installs two `uvicorn.access` log filters: `HealthCheckLogFilter` (silences 2xx `/health` probes) and `QuietAccessLogFilter` (silences 2xx list/retrieve/modify calls, which the service layer already logs; create/delete/search stay visible, and every non-2xx line still shows up).

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Static methods `upload_file`, `list_files`, `get_file_by_id`, `delete_file` — all four scope by the caller's `api_key`, `get_file_by_id` included (it previously read by `id` alone, letting any valid key fetch metadata for another key's files). Upload flow: generate `file_id`, build object path `{api_key}/uploads/{uuid}_{filename}`, upload bytes to MinIO via `get_io_executor()`, then insert the Postgres metadata row. If the Postgres insert fails after a successful MinIO upload, the object is left orphaned (logged as a warning, not cleaned up) and `PostgresConnectionException` is raised.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Static methods `create`, `list`, `get`, `modify`, `delete`, `search`.

- `create` rejects more than one `file_id` before touching the database (`UnsupportedMultipleFilesException`, 400 — "reject upfront instead of polling into a store that never finishes"), writes the Postgres row with the provider it was created with, then enqueues `ingest_vector_store_files.kiq(...)` together with `request_id` and `inject_trace_context()`.
- `search` reads the row to learn its `vector_store_type`, converts `search_request.filters` into the backend-neutral tree via `normalize_search_filter`, opens the root trace span, and runs a `RetrievalPipeline` in-process. Results come back as `RetrievedChunk` and are converted to the API shape by `convert_retrieved_chunks_to_search_results`.
- Both paths resolve their store through `VectorStoreFactory.get_store(collection_name, provider)` — passing the provider recorded on the row, so a collection created on a backend this deployment no longer connects fails loudly instead of being queried against the wrong one.
- `_calculate_file_counts(status)` **derives** the `file_counts` object from the store's single status field — `completed=1` when the store completed, `failed=1` when it failed, and never anything else. There is no per-file state to report from, which is why the response shape is OpenAI-compatible while the numbers can only ever be 1/0 (see [Design Decisions](DESIGN_DECISIONS.md#known-gaps)).

### `IngestionService` (`ingestion/ingestion_service.py`)

The background task's business logic, deliberately free of any TaskIQ import so it can be called and tested without a running broker. `ingest_vector_store_files(...)`:

1. Guard: `chunking_strategy` is one of `("auto", "static")`, else log, `_mark_failed`, and raise — an unrecognised value means `VectorStoreService.create` and this worker have drifted apart, and failing beats silently skipping the ingest
2. `PostgresFileStore.check_existing_files` — `file_ids` were requested but none of them exist? log, `_mark_failed`, and raise. Only a store created with **no** `file_ids` at all skips ahead to `completed` with `usage_bytes=0`
3. Fetch `get_total_bytes` + `get_metadata_for_files`, both keyed on `existing_file_ids` so a vanished id counts toward neither
4. Guard: exactly one file, else log, `_mark_failed`, and raise — a second line of defense behind the API-level rejection. `0` is still reachable here: `get_metadata_for_files` drops rows whose `metadata` is `NULL`, so a file that exists without metadata lands in this branch
5. `_ingest_single_file` builds the pipeline via `build_ingestion_pipeline(...)` and runs it against an `IngestionContext`, passing `trace_context` as the parent carrier, and returns `context.num_inserted`
6. Guard: `num_inserted > 0`, else log, `_mark_failed`, and raise — the pipeline runs to the end on a file it can extract no text from, and an empty collection is not something to call `completed`
7. Mark `completed` with `usage_bytes`; any failure marks `failed` and re-raises

Every gate above exists for one reason: `completed` is the client's signal that the collection is searchable, so each path that would leave it empty fails instead.

## Pipeline layer (`app/pipelines/`)

### The framework (`base.py`, `pipeline.py`)

`BaseStage[ContextT]` is the contract: a `name` (the span name), an `observation_type`, an `async run(context)`, plus two optional hooks — `emits_span(context)` (decided *before* `run`, so it can inspect what earlier stages produced; returning `False` still runs the stage, it just keeps a no-op out of the trace) and `span_attributes(context)` (read *after* `run` succeeded).

`Pipeline[ContextT]` runs the stages in order under a single parent span and is **the only place that opens spans**. Stages never import the tracing module. Subclasses override `root_attributes()` (set before any stage) and `result_attributes()` (set after all stages succeed). `run(context, parent_carrier)` accepts a `TraceCarrier` so a pipeline started from a queued job appears inside the trace of the request that queued it.

The payoff: the trace shape is a property of the pipeline, and it stays correct when stages are added, removed, or reordered.

### Ingestion (`pipelines/ingestion/`)

`IngestionContext` is one mutable dataclass threaded through every stage — inputs (`vector_store_id`, `file_id`, `file_metadata`, chunking params), progressively filled results (`raw_bytes` → `text` → `chunks` → `num_inserted`), and a free-form `metrics` dict for numbers that belong on a span but not in pipeline state. Convenience properties (`filename`, `file_extension`, `storage_bucket`, `storage_path`) read out of `file_metadata`.

| Stage | Does | Span notes |
|---|---|---|
| `DownloadStage` | Fetches bytes from MinIO under `get_download_semaphore()`, transfer on `get_io_executor()` | bucket/path, filename, `file.size_bytes` |
| `ParseStage` | `ParsingService.parse(bytes, ext)` → text (Markdown) | records the provider that handled it, `text.num_chars` |
| `ChunkStage` | `ChunkingService.split_text(text)` → chunks, on `get_cpu_executor()` | strategy/provider, `chunks.count`, `chunks.avg_chars` |
| `EmbedAndIndexStage` | `ensure_collection(embedding_dim, with_sparse)` once, then embeds + upserts **one batch at a time** | whether the collection was created, `embedding.dims`, `embedding.sparse_enabled`/`sparse_model`, `embed`/`index` wall-clock, `batch.*` |

`EmbedAndIndexStage` is the former `EmbedStage` + `IndexStage` merged into one streaming loop:

- Chunks are split into `EMBEDDING_UPLOAD_BATCH_SIZE` batches, and **one** `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY)` covers that batch's embed *and* its upsert — no more "embed the whole file, then write"; the first batch is already being written while later ones are still embedding.
- `Document` objects are built *after* the semaphore slot is acquired, so peak memory is `batch_size × concurrency` batches' worth of chunks/vectors/Documents, not the whole file's.
- `embedding_dim` comes from `get_dense_embedding_dim()` — the dimension `EmbeddingService.check_connection()` cached at startup — so the collection can be created *before* the first embed call. That is the precondition streaming needs: the old `IndexStage` had to infer `embedding_dim` from `context.embeddings[0]`, which meant waiting for embedding to finish.
- `embed_wall_clock_s` / `index_wall_clock_s` are computed by `_union_duration()`, which merges overlapping intervals, because summing each concurrent batch's raw duration would double-count.
- With a `sparse_embed_fn` supplied, each batch is embedded twice — dense and sparse in one `asyncio.gather`, so the batch costs the slower server rather than both in turn — and both vectors are written onto the same point, so hybrid retrieval needs no second pass over the file. Whether that happens is decided by asking the *collection* (`supports_sparse()`) after `ensure_collection`, not by trusting config: a collection created before sparse was switched on has no sparse field, neither backend can add one, and upserting a sparse vector it cannot hold would fail every batch. The outcome lands in `metrics["sparse_enabled"]`.
- Note: the merged stage does not override `observation_type`, so it shows up in Langfuse as `ObservationType.SPAN`, unlike the previous `EmbedStage` (`EMBEDDING`).

`IngestionContext` follows suit: there is no `embeddings` field any more — vectors live only within a single batch, and `num_inserted` is the last step's only output.

`build_ingestion_pipeline(...)` is the single place deciding which stages run and in what order. The `ChunkingService` is built per pipeline rather than at startup, because chunk size and overlap come from the vector store's create request.

### Retrieval (`pipelines/retrieval/`)

`RetrievalContext` carries the query, `limit`, neutral `filters`, and `score_threshold` in; `dense_vector`, `sparse_vector` (hybrid only), `candidates` (hits keyed by retriever name), and `results` out.

| Stage | Does | Span notes |
|---|---|---|
| `EmbedQueryStage` | Embeds the query text | `ObservationType.EMBEDDING` |
| `RetrieveStage` | Runs every retriever concurrently, keys hits by retriever name | `ObservationType.RETRIEVER`; merges each retriever's `span_attributes()` under its own name prefix |
| `FuseStage` | Merges candidate lists into the final ranked list | skips its span when there is nothing to fuse |

`BaseRetriever` is the seam hybrid search is added at — `RetrievalQuery` carries every representation of the query at once (raw text, dense vector, sparse vector), so each retriever picks the one it understands without the pipeline knowing which that is. `DenseRetriever` and `HybridRetriever` are the implementations; both return `[]` for a missing collection rather than raising, since a vector store row can exist in Postgres before ingestion has created the collection.

`HybridRetriever` is **one** retriever, not two: it hands the store both vectors in a single `retrieve()` call and the backend fuses the dense and sparse branches server-side by reciprocal rank — Qdrant with `prefetch` + `FusionQuery(RRF)`, Milvus with `hybrid_search` + `RRFRanker`. That keeps hybrid to one round-trip on either engine, and means the two incomparable score scales — cosine and term-weight dot product — never have to be reconciled, only their ranks. Its `used_sparse` span attribute is the first thing to check when hybrid results look identical to dense ones. Two `config.yaml` keys tune the fusion, both deployment-wide rather than per-request and both reported on the span: `retrieval.hybrid_prefetch_multiplier` (default 2) sets how deep each branch reaches before fusion, and `retrieval.rrf_k` (empty = the backend's own default, 60 on both) sets how much being found by both branches is worth against one strong single-branch hit. Left empty on Qdrant, `rrf_k` sends the same `FusionQuery(fusion=RRF)` as before it was tunable; a value switches to `RrfQuery(rrf=Rrf(k=…))`, so only an opted-in deployment meets the newer request shape.

`BaseFusion` is the matching seam for merging in the process instead. `PassthroughFusion` is the only implementation — every search runs one retriever, hybrid included — and **raises** if handed more than one candidate list rather than silently dropping results. The seam stays for the case that changes: a backend that cannot fuse server-side.

`build_retrieval_pipeline(vector_store, embed_fn, search_type, sparse_embed_fn)` resolves a `SearchType` into a `_RetrievalPlan(retrievers, fusion)`. Retrievers and fusion are chosen *together* so an invalid combination cannot be assembled by accident. The sparse embedder is withheld on a dense search so the query is not embedded twice for a representation nothing will read.

`hybrid_unavailable_reason(vector_store)` is the single source of truth for whether hybrid can run: it returns `None` when it can, otherwise a sentence saying which half is missing — `SPARSE_EMBEDDING_ENABLED` off (server-side), or a collection carrying no sparse vectors (store-side). The second condition is not implied by the first — a store ingested before sparse was switched on has no sparse field, and neither backend can add one to a live collection (Qdrant's vector config and Milvus' schema are both fixed at creation) — so the collection is asked rather than assumed (`supports_sparse()`: Qdrant reads the collection's vector config, Milvus reads `describe_collection`). It returns a reason rather than a bool because the two failures need different fixes: enable sparse, versus re-ingest the store.

`resolve_search_type(vector_store)` is the thin wrapper over it for callers with no opinion: `HYBRID` when the reason is `None`, `DENSE` otherwise. Resolving per search is what lets old dense-only stores and new hybrid ones be served by the same process.

`VectorStoreService.search` picks from three inputs in order: an explicit `search_type` argument (internal callers), then the request's `search_type` field (`"auto" | "dense" | "hybrid"`, defaulting to `"auto"`), then `resolve_search_type`. A pinned `HYBRID` is re-checked against `hybrid_unavailable_reason` and rejected with `UnsupportedSearchTypeException` (400) rather than falling back to dense — silently answering a hybrid request with dense results would leave the caller measuring retrieval quality on a configuration they do not think they are running. The whole decision happens before the trace span opens, so the trace records the search that ran.

`chunks_to_trace_json` renders hits for a span attribute as `{chunk_id, score}` only: a trace is not the place to duplicate document contents.

## Component layer (`app/components/`)

Every package here follows the same shape — a `base.py` declaring the interface, a `provider/` holding implementations, and a facade service whose `from_settings()` picks one from config. Nothing here orchestrates anything or knows about HTTP.

| Package | Interface | Providers | Selected by |
|---|---|---|---|
| `parsing/` | `BaseParsingProvider` | `LlamaParseProvider` (`.pdf`), `UnstructuredProvider` (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) | `PDF_PARSER_PROVIDER` (PDF only) |
| `chunking/` | `BaseChunkingProvider` | `ChonkieProvider`, `LangchainProvider` | `CHUNKING_PROVIDER` |
| `embedding/` | `BaseEmbeddingProvider` | `OpenAIEmbeddingProvider`, `TEIEmbeddingProvider` | `EMBEDDING_PROVIDER` |
| `embedding/` | `BaseSparseEmbeddingProvider` | `VLLMSparseEmbeddingProvider` | `SPARSE_EMBEDDING_PROVIDER` (only when `SPARSE_EMBEDDING_ENABLED`) |

Both embedding providers are HTTP clients — no model is loaded in this process, so neither the web service nor the worker needs a GPU. `OpenAIEmbeddingProvider` posts to `{DENSE_EMBEDDING_URL}/embeddings`, `TEIEmbeddingProvider` to `{DENSE_EMBEDDING_URL}/embed`, and `VLLMSparseEmbeddingProvider` to `/tokenize` + `/pooling` on the sparse root. What answers those URLs is a separate deployment: [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) is the reference one — vLLM serving `Qwen/Qwen3-Embedding-0.6B` on `:8100` and `BAAI/bge-m3` on `:8101`, matching the defaults these settings ship with (see [Embedding Server](README.md#embedding-server)).

`ParsingService.from_settings()` maps extensions to *provider factories*, not instances: `.pdf` goes to the backend named by `PDF_PARSER_PROVIDER`, and **every other format** (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) goes to the Unstructured API. Because `.txt`/`.md` are registered against the *same factory object*, they share a single provider instance — that is why the `_instances` dict is keyed by factory rather than by extension. The PDF backend's name is validated at startup (so a typo fails fast), but providers are only constructed on first use, and `UNSTRUCTURED_API_KEY` is likewise only checked then — a deployment that ingests nothing but PDFs should not fail to start over a missing Unstructured key. `supports()`, `supported_extensions`, and `provider_for()` expose the registry; an unmapped extension raises `ValueError("Unsupported file format: ...")`.

`UnstructuredProvider` calls `partition_via_api` (the `unstructured` library is synchronous, so it runs via `asyncio.to_thread`) and gets back a JSON element list — the API supports no Markdown response format, only `application/json` or `text/csv`. The provider renders that element list to Markdown locally: `Title` → heading by `category_depth`, `ListItem` → indented bullet, `Table` → a Markdown table built from the `text_as_html` metadata (via BeautifulSoup). That way every parsing provider returns the same output shape (Markdown), matching `LlamaParseProvider` on PDFs, and downstream chunking never has to care which format the text came from.

Note the mismatch between the two allow-lists: upload-time `ALLOWED_EXTENSIONS` permits `.csv`, `.json`, and `.gif` — those can be **uploaded** as Files but have no registered parsing provider, so ingestion raises `ValueError`. Conversely `.md` and `.doc` **can be parsed** but are not in `ALLOWED_EXTENSIONS`, and `validate_file_type` gates on extension with no escape hatch, so they are always rejected with a 415 at upload.

`ChunkingService` exposes `strategy_name` and `async split_text(text)`. The Chonkie provider supports `character | sentence | recursive | token` (default `recursive`, `chunk_size=800`, `chunk_overlap=400`). At the API level, `VectorStoreCreateRequest.chunking_strategy` only exposes `"auto"` or `"static"`, so the finer strategies aren't reachable through the public API — and neither is the internal strategy itself, which nothing outside `ChonkieChunkingConfig`'s default can change. Two consequences follow from that default, both invisible to the caller: `_create_chunker` passes `chunk_overlap` to the token and sentence chunkers only, so on `recursive` the request's `chunk_overlap_tokens` is **dropped**; and `tokenizer="character"` means `max_chunk_size_tokens` is counted in characters, not tokens (see [Design Decisions](DESIGN_DECISIONS.md#known-gaps)).

Both chunking providers `run_in_executor(get_cpu_executor(), ...)`: splitting is CPU-bound and would otherwise stall the event loop for every other task in the process. The CPU pool (`CPU_THREAD_POOL_SIZE`, default 4) is deliberately separate from the I/O pool (`IO_THREAD_POOL_SIZE`, default 32) — oversubscribing CPU-bound work only adds context switching, not throughput, while sharing one pool lets a slow MinIO transfer make chunking queue behind it, and vice versa.

## Data layer (`app/db/`)

| Package | Class(es) | Backs | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Uploaded file bytes (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | `files` + `vector_stores` metadata tables (ownership by `api_key`, status, `vector_store_type`, JSONB metadata) | `postgres` (5432) |
| `vector_store/` | `BaseVectorStoreConnection`, `BaseAsyncVectorStore`, `VectorStoreFactory` | One collection per vector store (`collection_name == vector_store_id`) | none — external (Qdrant `6333`, Milvus `19530`), outside this Compose stack |

Every MinIO operation (`upload_file`, `download_file`, `delete_file`) takes an `executor` argument and is called with `get_io_executor()` rather than borrowing the event loop's default executor. `download_file` delegates to the `_fetch_object` helper, which folds `get_object()` + `.read()` + `close()` into **one** unit of work on a worker thread: `get_object()` only opens the stream (headers), the entire real transfer happens in `.read()`, so offloading just the open still blocks the event loop in proportion to file size.

### The Postgres schema (`db/postgres/schema/`)

Two tables and two indexes, all `CREATE ... IF NOT EXISTS` and applied by `_create_table()` — which runs on the **web** process only. The worker's `init_postgres()` builds a pool and stops there, so the web service must have booted at least once before a worker can write.

| Table | Columns | Notes |
|---|---|---|
| `files` | `id` (PK, `file-{8 hex}`), `api_key`, `bytes`, `purpose`, `created_at`, `expires_at`, `content_type`, `metadata` JSONB | `metadata` holds `{filename, minio_bucket, minio_path, etag}` — the pointer back to the object, which is why deleting a row without deleting the object orphans it |
| `vector_stores` | `id` (PK, `vs_{32 hex}`), `api_key`, `name`, `description`, `created_at`, `last_active_at`, `status`, `usage_bytes`, `metadata` JSONB, `expires_at`, `expires_after`, `chunking_strategy` JSONB, `vector_store_type` | `status` is the whole store's (`in_progress`/`completed`/`failed`), and `vector_store_type` is what makes several backends connectable at once — every row names the engine holding its collection |

Both indexes are composite and ordered to match the list queries exactly: `idx_files_api_key_purpose_created_at_id` on `(api_key, purpose, created_at, id)` and `idx_vector_stores_api_key_created_at_id` on `(api_key, created_at, id)`. Ownership comes first because every query is scoped by `api_key`, and `created_at, id` is the cursor-pagination sort key — `id` breaking ties so two rows created in the same instant can't be returned twice or skipped. Adding a list filter that isn't a prefix of one of these leaves it unindexed.

There is no `vector_store_files` table: a store's file relationship lives only in the ingestion task's arguments, which is the structural reason multi-file ingestion isn't just a lifted validation check (see [Design Decisions](DESIGN_DECISIONS.md#known-gaps)).

### The vector store abstraction (`db/vector_store/`)

Two abstractions in `base.py`:

- `BaseVectorStoreConnection` — a long-lived connection created once at startup (`from_settings()`, `client`, `check_connection()`, `close()`); the equivalent of a connection pool
- `BaseAsyncVectorStore` — operations scoped to one collection, constructed per use from a client

`ensure_collection(embedding_dim, with_sparse)` is deliberately **separate** from `insert_documents`. Folding creation into the insert path forces every concurrent batch to race on a check-then-act, which is exactly why the previous ingest code had to run its first batch alone. Callers now create the collection once up front, after which inserts are pure and safely parallel. Both implementations still tolerate losing that race — if creation fails and the collection now exists, another process won and the call returns `False` instead of raising, so two workers starting the same store cannot fail each other.

The companion point: the `embedding_dim` passed in comes from `get_dense_embedding_dim()` (cached at startup), not from the first embedding vector. That keeps the vector store contract from forcing callers to embed before creating a collection — which is precisely what lets `EmbedAndIndexStage` embed and write in a streaming fashion.

`types.py` holds the backend-neutral shapes — `RetrievedChunk`, and a filter tree of `FieldCondition` / `FilterGroup` with `FilterOperator` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`) and `FilterCombinator` (`and`, `or`). Nothing in this module may import a vendor SDK; these are the only shapes the service layer sees, so swapping Qdrant for Milvus never reaches above `app.db`.

`VectorStoreFactory` resolves a provider name to an implementation. Backends are addressed by *module path* and imported on first use rather than at module level — that keeps the import graph acyclic, and means a backend whose SDK isn't installed only fails when it's actually asked for. Startup pushes live connections down via `register_connection()`; the factory never imports `app.startup`. `get_store(collection_name, provider)` takes the provider recorded on the vector store row; `get_connection` raises a `RuntimeError` naming the fix when a store references a provider this deployment doesn't run.

Each backend supplies a `filter_translator.py` rendering the neutral tree into its own language (`to_qdrant_filter`, `to_milvus_expression`).

**Qdrant** (`provider/qdrant/`) — `AsyncQdrantVectorStore` creates its collection with HNSW + configurable quantization (`scalar`/`binary`/`product`), `indexing_threshold=0` at creation raised to `20000` after bulk insert to avoid indexing overhead mid-load. `retrieve` runs one query per query vector, gathered in parallel, and returns `RetrievedChunk`. Its named vector fields come from `DENSE_VECTOR_NAME` / `SPARSE_VECTOR_NAME`, which fold the model id through `_vector_name()` — Qdrant forbids `/` and `:` in a vector name and every model id has a `/`, so `Qwen/Qwen3-Embedding-0.6B` is stored as `Qwen_Qwen3-Embedding-0.6B`. Renaming that mapping strands every collection created before it, since `using=` would no longer match a field that exists.

**Milvus** (`provider/milvus/`) — `AsyncMilvusVectorStore` creates its collection with HNSW over a `dense_vector` field plus, when sparse is on, a `SPARSE_INVERTED_INDEX` over `sparse_vector`; documents are rows carrying `page_content` and a `metadata` JSON field, so payloads stay compatible with the Qdrant backend. `retrieve` sends every query vector in one request (`search`, or `hybrid_search` with `RRFRanker` when sparse vectors are given), unlike Qdrant's parallel fan-out. Two engine differences are absorbed here rather than exposed: names are folded to what Milvus accepts — current ids (`vs_a1b2…`) pass through as the collection name unchanged while legacy hyphenated ones (`vs-a1b2…`) still fold to `vs_a1b2…`, and vector fields are fixed names rather than the model id, which Milvus would reject — and a search that finds the collection unloaded (as after a server restart) loads it and retries once. Reads use `Strong` consistency to match Qdrant's read-your-writes.

## Configuration (`app/core/config/`)

Two layers merged per domain module:

1. **pydantic-settings** (`settings.py`) — reads `.env` / real environment variables. Required fields (no default) are the API keys and the Postgres/MinIO/Langfuse credentials; the vector store's are **conditionally** required, checked by `validate_vector_store_credentials` for `VECTOR_STORE_PROVIDER` only (`QDRANT_URL` + `QDRANT_API_KEY`, or `MILVUS_URI`) — a Qdrant-only deployment never has to fill in Milvus, and filling a backend in is itself what connects it (`enabled_vector_store_providers`). Validators enforce `API_VERSION` starts with `v`, ports are positive, secrets are non-empty, `LOG_LEVEL`/`LOG_FORMAT` are in an allowed set, and each of `EMBEDDING_PROVIDER` / `SPARSE_EMBEDDING_PROVIDER` / `CHUNKING_PROVIDER` / `PDF_PARSER_PROVIDER` / `VECTOR_STORE_PROVIDER` names a known backend — so a typo fails at startup, not at first use. `_VECTOR_STORE_REQUIRED_SETTINGS` is one dict serving as both the allowed-value set and the per-backend credential list, so adding a backend is a single edit.
2. **YAML** (`config/config.yaml`, loaded via `YamlConfigLoader`) — stable tunables: `api.num_workers`, `storage.uploaded_file_bucket`/`max_file_size`/`io_thread_pool_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`, `ingestion.cpu_thread_pool_size`/`download_concurrency`, `retrieval.hybrid_prefetch_multiplier`/`rrf_k`. The two retrieval keys are validated at **import** (`retrieval.py` raises on a non-integer or a value below 1) rather than by pydantic, since they never come from the environment.

Per-domain modules (`database.py`, `storage.py`, `models.py`, `embedding.py`, `redis.py`, `langfuse.py`, `api.py`) combine both sources into flat constants importable from `app.core.config`. Full parameter list: [Configuration Reference](../CONFIGURATION.md).

## Tracing (`app/core/tracing/`)

Langfuse via **OpenTelemetry OTLP** exporter — `init_tracing()` builds a `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter(...))` pointed at `{LANGFUSE_BASE_URL}/api/public/otel/v1/traces` with Basic-Auth from the public/secret key pair.

- `tracing.py` — `traced_span(name, attributes, parent_carrier)` context manager (auto-sets `Status.OK`/`Status.ERROR`, records exceptions) and `set_span_attributes()`
- `attributes.py` — Langfuse's attribute names as constants (`TRACE_INPUT`, `TRACE_TAGS`, `TRACE_USER_ID`, `OBSERVATION_TYPE`, …), the `ObservationType` enum (`SPAN`, `EMBEDDING`, `RETRIEVER`, …), and the `observation_metadata()` / `trace_metadata()` helpers that drop `None` values
- `propagation.py` — `inject_trace_context()` / `extract_trace_context()` over a `TraceCarrier` (W3C traceparent), which is what lets a worker-side pipeline nest inside the HTTP request's trace

Span structure: `VectorStoreService.search` opens the root span (`POST /v1/vector_stores/{id}/search`) carrying the trace-level attributes, and `RetrievalPipeline` emits one observation per stage inside it. Ingestion mirrors this from the worker side, parented by the propagated carrier. Both the web app and `app/tasks/broker.py` call `init_tracing()` independently at their own startup.

## Exceptions (`app/exceptions/`)

Root `AppBaseException(status_code, response: BaseResponse, log_message)`. Domain subclasses: `auth/` (`APIKeyIncorrectException`, `BearerMissingException` — 401), `file/` (`FileSizeLimitExceededException` 413, `FileNotFoundException` 404), `postgres/` (`PostgresConnectionException` 503), `vector_store/` (`VectorStoreNotFoundException` 404, `WrongPrefixVectorstoreException` 400, `UnsupportedMultipleFilesException` 400). `common_exception_handler` (registered globally) logs at ERROR/WARNING depending on status and returns `JSONResponse({message, type, params, code})` — the OpenAI-style error envelope. Plain `HTTPException`s (e.g. 415 from `app/api/dependencies.py`) fall through to FastAPI's default handler instead.

## Schemas (`app/schemas/`)

- `base/` — shared `BaseModel` (`extra="forbid"`), `PaginationParams`, generic `PaginatedResponse[T]`.
- `file/` — `FileObject`, `FileListObject`, request/response variants, `UploadingStatus` enum.
- `vector_store/` — `VectorStoreCreateRequest`/`ModifyRequest`/`QueryRequest`/`SearchRequest`, chunking-strategy union types, `ComparisonFilter`/`CompoundFilter`, `VectorStoreObject`, `VectorStoreSearchResponse`.
- `vector_store/types.py` — `VectorStoreType` (`qdrant`, `milvus`) and `SearchType` (`dense`, `hybrid`; pinned by the caller or resolved per search when they send `"auto"`). `SearchType` names the whole retrieval shape rather than exposing a retriever list plus a fusion strategy separately, because those two always have to agree — naming the combination keeps the invalid ones unrepresentable.
- `chunking/` — `ChunkingStrategy` enum, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Startup / bootstrap (`app/startup.py`)

Manual service-locator globals set by `init_embed_model`, `init_parsing_service`, `init_postgres`, `init_minio`, `init_vector_store`, `init_io_executor`, `init_cpu_executor`, `init_download_semaphore` and read via matching getters (`get_dense_embedding`, `get_dense_embedding_dim`, `get_parsing_service`, `get_postgres_pool`, `get_postgres_client`, `get_minio_service`, `get_vector_store_connection`, `get_io_executor`, `get_cpu_executor`, `get_download_semaphore`).

`init_embed_model` builds `EmbeddingService.from_settings()` for the configured provider and smoke-tests it; that same `check_connection()` call caches the vector dimension, read later via `get_dense_embedding_dim()` (which raises `RuntimeError` if called before init). `init_sparse_embed_model` mirrors it for the lexical side — same build-then-probe shape, so an unreachable sparse server fails the boot rather than the first ingestion — except it is opt-in: with `SPARSE_EMBEDDING_ENABLED` false it logs and leaves the service unset, and `get_sparse_embed_model()` then raises `RuntimeError` (use `is_sparse_embedding_enabled()` to branch on availability instead). `init_parsing_service` builds the providers once so a parsing backend's client is reused across files rather than rebuilt per ingestion. `init_vector_store` builds a connection for every backend whose settings are filled in, verifies each, and registers them with `VectorStoreFactory` — connecting more than one is what lets stores on different engines be served side by side. Only `VECTOR_STORE_PROVIDER` failing to connect stops the boot; another backend is skipped with a warning, since it is connected merely by having credentials present. `wait_for_postgres` retries the pool 5× at 0.5s and re-raises the last failure rather than continuing as if Postgres were reachable.

Used identically — but independently instantiated — by both `app/app.py` (web) and `app/tasks/broker.py` (worker), so there are no worker-local globals.

## Background worker (`app/tasks/`)

`broker.py` owns only the broker lifecycle: `RedisStreamBroker` + `RedisAsyncResultBackend` over `REDIS_URL`, with `WORKER_STARTUP`/`WORKER_SHUTDOWN` hooks that run `_initialize_services()` once (guarded by the `_initialized` flag) and close the Postgres pool plus `VectorStoreFactory.close_all()` on the way out. `_initialize_services()` mirrors the startup half of `app.app`'s lifespan but adds three things the web process does not need — `init_cpu_executor()`, `init_download_semaphore()`, and `init_parsing_service()` — because the worker is where the whole ingestion pipeline runs. Keeping the task in a separate module is what lets `app.tasks.broker:broker` be the deploy entrypoint without importing the ingestion pipeline just to start the process.

`ingestion_task.py` holds the one task, `ingest_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id, trace_context, vector_store_type)`. It is orchestration only: bind `request_id_ctx`, delegate to `IngestionService`, log and re-raise so TaskIQ sees the failure, reset the contextvar.

See [Flow](FLOW.md) for the full sequence diagrams.
