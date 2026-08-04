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
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters` applied, `ranking_options` not yet |

### `dependencies.py`

`validate_file_size` (rejects over `MAX_FILE_SIZE` MB), `validate_file_type` (checks MIME + extension + their consistency via `MIME_TYPE_MAPPING`), composed as `validate_file` — used as `Depends(validate_file)` on the upload endpoint.

### `security.py`

`verify_api_key` requires an `Authorization: Bearer <token>` header, compares the token against the single configured `FASTAPI_API_KEY`, and returns the token as `api_key` — which is then used to scope Postgres rows. Because there is exactly one valid token, this is effectively single-tenant auth even though the schema is shaped for multi-tenant ownership (see [Design Decisions](DESIGN_DECISIONS.md)).

### `middleware.py`

`RequestIDMiddleware` is a raw ASGI middleware (not `BaseHTTPMiddleware` — that would reset the contextvar before uvicorn's access logger fires). It reuses a client-supplied `X-Request-Id` header if present, else generates `req_{uuid4().hex}`, binds it to `request_id_ctx` (a `contextvars.ContextVar` in `app/core/request_context.py`) for the request's duration, and echoes it back on the response. The same ID is passed into `ingest_vector_store_files.kiq(...)` and re-bound inside the worker task, so a single request ID threads through HTTP logs and the async ingestion job that request triggered.

`app/app.py` also defines `GET /health` (excluded from OpenAPI schema), registers `RequestIDMiddleware`, the global `AppBaseException` handler, and a startup event that brings up tracing → embed model → Postgres (pool + `wait_for_postgres` + table creation) → vector store → MinIO → I/O pool, in that order. It also installs two `uvicorn.access` log filters: `HealthCheckLogFilter` (silences 2xx `/health` probes) and `QuietAccessLogFilter` (silences 2xx list/retrieve/modify calls, which the service layer already logs; create/delete/search stay visible, and every non-2xx line still shows up).

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Static methods `upload_file`, `list_files`, `get_file_by_id`, `delete_file` — all four scope by the caller's `api_key`, `get_file_by_id` included (it previously read by `id` alone, letting any valid key fetch metadata for another key's files). Upload flow: generate `file_id`, build object path `{api_key}/uploads/{uuid}_{filename}`, upload bytes to MinIO via `get_io_executor()`, then insert the Postgres metadata row. If the Postgres insert fails after a successful MinIO upload, the object is left orphaned (logged as a warning, not cleaned up) and `PostgresConnectionException` is raised.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Static methods `create`, `list`, `get`, `modify`, `delete`, `search`.

- `create` rejects more than one `file_id` before touching the database (`UnsupportedMultipleFilesException`, 400 — "reject upfront instead of polling into a store that never finishes"), writes the Postgres row with the provider it was created with, then enqueues `ingest_vector_store_files.kiq(...)` together with `request_id` and `inject_trace_context()`.
- `search` reads the row to learn its `vector_store_type`, converts `search_request.filters` into the backend-neutral tree via `normalize_search_filter`, opens the root trace span, and runs a `RetrievalPipeline` in-process. Results come back as `RetrievedChunk` and are converted to the API shape by `convert_retrieved_chunks_to_search_results`.
- Both paths resolve their store through `VectorStoreFactory.get_store(collection_name, provider)` — passing the provider recorded on the row, so collections created under an older `VECTOR_STORE_PROVIDER` keep working.

### `IngestionService` (`ingestion/ingestion_service.py`)

The background task's business logic, deliberately free of any TaskIQ import so it can be called and tested without a running broker. `ingest_vector_store_files(...)`:

1. `PostgresFileStore.check_existing_files` — if none of the referenced files still exist, skip straight to marking the store `completed` with `usage_bytes=0`
2. Fetch `get_total_bytes` + `get_metadata_for_files`
3. Guard: exactly one file, else log, `_mark_failed`, and raise — a second line of defense behind the API-level rejection, so an empty store is never reported `completed`
4. `_ingest_single_file` builds the pipeline via `build_ingestion_pipeline(...)` and runs it against an `IngestionContext`, passing `trace_context` as the parent carrier
5. Mark `completed` with `usage_bytes`; any failure marks `failed` and re-raises

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
| `EmbedAndIndexStage` | `ensure_collection(embedding_dim)` once, then embeds + upserts **one batch at a time** | whether the collection was created, `embedding.dims`, `embed`/`index` wall-clock, `batch.*` |

`EmbedAndIndexStage` is the former `EmbedStage` + `IndexStage` merged into one streaming loop:

- Chunks are split into `EMBEDDING_UPLOAD_BATCH_SIZE` batches, and **one** `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY)` covers that batch's embed *and* its upsert — no more "embed the whole file, then write"; the first batch is already being written while later ones are still embedding.
- `Document` objects are built *after* the semaphore slot is acquired, so peak memory is `batch_size × concurrency` batches' worth of chunks/vectors/Documents, not the whole file's.
- `embedding_dim` comes from `get_dense_embedding_dim()` — the dimension `EmbeddingService.check_connection()` cached at startup — so the collection can be created *before* the first embed call. That is the precondition streaming needs: the old `IndexStage` had to infer `embedding_dim` from `context.embeddings[0]`, which meant waiting for embedding to finish.
- `embed_wall_clock_s` / `index_wall_clock_s` are computed by `_union_duration()`, which merges overlapping intervals, because summing each concurrent batch's raw duration would double-count.
- Note: the merged stage does not override `observation_type`, so it shows up in Langfuse as `ObservationType.SPAN`, unlike the previous `EmbedStage` (`EMBEDDING`).

`IngestionContext` follows suit: there is no `embeddings` field any more — vectors live only within a single batch, and `num_inserted` is the last step's only output.

`build_ingestion_pipeline(...)` is the single place deciding which stages run and in what order. The `ChunkingService` is built per pipeline rather than at startup, because chunk size and overlap come from the vector store's create request.

### Retrieval (`pipelines/retrieval/`)

`RetrievalContext` carries the query, `limit`, neutral `filters`, and `score_threshold` in; `dense_vector`, `candidates` (hits keyed by retriever name), and `results` out.

| Stage | Does | Span notes |
|---|---|---|
| `EmbedQueryStage` | Embeds the query text | `ObservationType.EMBEDDING` |
| `RetrieveStage` | Runs every retriever concurrently, keys hits by retriever name | `ObservationType.RETRIEVER`; merges each retriever's `span_attributes()` under its own name prefix |
| `FuseStage` | Merges candidate lists into the final ranked list | skips its span when there is nothing to fuse |

`BaseRetriever` is the seam for hybrid search — `RetrievalQuery` deliberately carries the raw query text *and* the dense vector, so a retriever doing its own tokenising has what it needs without the pipeline knowing which representation each retriever consumes. `DenseRetriever` is the one implementation; a missing collection returns `[]` rather than raising, since a vector store row can exist in Postgres before ingestion has created the collection.

`BaseFusion` is the matching seam for merging. `PassthroughFusion` is the only implementation and **raises** if handed more than one candidate list, rather than silently dropping results — adding a second retriever forces you to choose a real fusion strategy.

`build_retrieval_pipeline(vector_store, embed_fn, search_type)` resolves a `SearchType` into a `_RetrievalPlan(retrievers, fusion)`. Retrievers and fusion are chosen *together* so an invalid combination cannot be assembled by accident. Search type is a per-call argument, not configuration — two queries against the same store can reasonably want different retrieval.

`chunks_to_trace_json` renders hits for a span attribute as `{chunk_id, score}` only: a trace is not the place to duplicate document contents.

## Component layer (`app/components/`)

Every package here follows the same shape — a `base.py` declaring the interface, a `provider/` holding implementations, and a facade service whose `from_settings()` picks one from config. Nothing here orchestrates anything or knows about HTTP.

| Package | Interface | Providers | Selected by |
|---|---|---|---|
| `parsing/` | `BaseParsingProvider` | `LlamaParseProvider` (`.pdf`), `UnstructuredProvider` (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) | `PDF_PARSER_PROVIDER` (PDF only) |
| `chunking/` | `BaseChunkingProvider` | `ChonkieProvider`, `LangchainProvider` | `CHUNKING_PROVIDER` |
| `embedding/` | `BaseEmbeddingProvider` | `OpenAIEmbeddingProvider`, `TEIEmbeddingProvider` | `EMBEDDING_PROVIDER` |

`ParsingService.from_settings()` maps extensions to *provider factories*, not instances: `.pdf` goes to the backend named by `PDF_PARSER_PROVIDER`, and **every other format** (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) goes to the Unstructured API. Because `.txt`/`.md` are registered against the *same factory object*, they share a single provider instance — that is why the `_instances` dict is keyed by factory rather than by extension. The PDF backend's name is validated at startup (so a typo fails fast), but providers are only constructed on first use, and `UNSTRUCTURED_API_KEY` is likewise only checked then — a deployment that ingests nothing but PDFs should not fail to start over a missing Unstructured key. `supports()`, `supported_extensions`, and `provider_for()` expose the registry; an unmapped extension raises `ValueError("Unsupported file format: ...")`.

`UnstructuredProvider` calls `partition_via_api` (the `unstructured` library is synchronous, so it runs via `asyncio.to_thread`) and gets back a JSON element list — the API supports no Markdown response format, only `application/json` or `text/csv`. The provider renders that element list to Markdown locally: `Title` → heading by `category_depth`, `ListItem` → indented bullet, `Table` → a Markdown table built from the `text_as_html` metadata (via BeautifulSoup). That way every parsing provider returns the same output shape (Markdown), matching `LlamaParseProvider` on PDFs, and downstream chunking never has to care which format the text came from.

Note the mismatch between the two allow-lists: upload-time `ALLOWED_EXTENSIONS` permits `.csv`, `.json`, and `.gif` — those can be **uploaded** as Files but have no registered parsing provider, so ingestion raises `ValueError`. Conversely `.md` and `.doc` **can be parsed** but are not in `ALLOWED_EXTENSIONS`, and `validate_file_type` gates on extension with no escape hatch, so they are always rejected with a 415 at upload.

`ChunkingService` exposes `strategy_name` and `async split_text(text)`. The Chonkie provider supports `character | sentence | recursive | token` (default `recursive`, `chunk_size=800`, `chunk_overlap=400`). At the API level, `VectorStoreCreateRequest.chunking_strategy` only exposes `"auto"` or `"static"`, so the finer strategies aren't reachable through the public API.

Both chunking providers `run_in_executor(get_cpu_executor(), ...)`: splitting is CPU-bound and would otherwise stall the event loop for every other task in the process. The CPU pool (`CPU_THREAD_POOL_SIZE`, default 4) is deliberately separate from the I/O pool (`IO_THREAD_POOL_SIZE`, default 32) — oversubscribing CPU-bound work only adds context switching, not throughput, while sharing one pool lets a slow MinIO transfer make chunking queue behind it, and vice versa.

## Data layer (`app/db/`)

| Package | Class(es) | Backs | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Uploaded file bytes (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | `files` + `vector_stores` metadata tables (ownership by `api_key`, status, `vector_store_type`, JSONB metadata) | `postgres` (5432) |
| `vector_store/` | `BaseVectorStoreConnection`, `BaseAsyncVectorStore`, `VectorStoreFactory` | One collection per vector store (`collection_name == vector_store_id`) | `qdrant` (6333/6334) |

Every MinIO operation (`upload_file`, `download_file`, `delete_file`) takes an `executor` argument and is called with `get_io_executor()` rather than borrowing the event loop's default executor. `download_file` delegates to the `_fetch_object` helper, which folds `get_object()` + `.read()` + `close()` into **one** unit of work on a worker thread: `get_object()` only opens the stream (headers), the entire real transfer happens in `.read()`, so offloading just the open still blocks the event loop in proportion to file size.

### The vector store abstraction (`db/vector_store/`)

Two abstractions in `base.py`:

- `BaseVectorStoreConnection` — a long-lived connection created once at startup (`from_settings()`, `client`, `check_connection()`, `close()`); the equivalent of a connection pool
- `BaseAsyncVectorStore` — operations scoped to one collection, constructed per use from a client

`ensure_collection(embedding_dim)` is deliberately **separate** from `insert_documents`. Folding creation into the insert path forces every concurrent batch to race on a check-then-act, which is exactly why the previous ingest code had to run its first batch alone. Callers now create the collection once up front, after which inserts are pure and safely parallel.

The companion point: the `embedding_dim` passed in comes from `get_dense_embedding_dim()` (cached at startup), not from the first embedding vector. That keeps the vector store contract from forcing callers to embed before creating a collection — which is precisely what lets `EmbedAndIndexStage` embed and write in a streaming fashion.

`types.py` holds the backend-neutral shapes — `RetrievedChunk`, and a filter tree of `FieldCondition` / `FilterGroup` with `FilterOperator` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`) and `FilterCombinator` (`and`, `or`). Nothing in this module may import a vendor SDK; these are the only shapes the service layer sees, so swapping Qdrant for Milvus never reaches above `app.db`.

`VectorStoreFactory` resolves a provider name to an implementation. Backends are addressed by *module path* and imported on first use rather than at module level — that keeps the import graph acyclic, and means a backend whose SDK isn't installed only fails when it's actually asked for. Startup pushes live connections down via `register_connection()`; the factory never imports `app.startup`. `get_store(collection_name, provider)` takes the provider recorded on the vector store row, so existing collections keep working after `VECTOR_STORE_PROVIDER` changes; `get_connection` raises a `RuntimeError` naming the fix when a store references a provider this deployment doesn't run.

Each backend supplies a `filter_translator.py` rendering the neutral tree into its own language (`to_qdrant_filter`, `to_milvus_expression`).

**Qdrant** (`provider/qdrant/`) — `AsyncQdrantVectorStore` creates its collection with HNSW + configurable quantization (`scalar`/`binary`/`product`), `indexing_threshold=0` at creation raised to `20000` after bulk insert to avoid indexing overhead mid-load. `retrieve` runs one query per query vector, gathered in parallel, and returns `RetrievedChunk`.

**Milvus** (`provider/milvus/`) — every method raises `NotImplementedError`. The class exists so the provider is already wired end to end (config validation, `VectorStoreType`, `VectorStoreFactory`, startup), which means enabling Milvus later is filling in bodies plus adding `pymilvus` — no changes above `app.db`.

## Configuration (`app/core/config/`)

Two layers merged per domain module:

1. **pydantic-settings** (`settings.py`) — reads `.env` / real environment variables. Required fields (no default) include API keys, Postgres/MinIO/Qdrant/Langfuse credentials. Validators enforce `API_VERSION` starts with `v`, ports are positive, secrets are non-empty, `LOG_LEVEL`/`LOG_FORMAT` are in an allowed set, and each of `EMBEDDING_PROVIDER` / `CHUNKING_PROVIDER` / `PDF_PARSER_PROVIDER` / `VECTOR_STORE_PROVIDER` names a known backend — so a typo fails at startup, not at first use.
2. **YAML** (`config/config.yaml`, loaded via `YamlConfigLoader`) — stable tunables: `api.num_workers`, `storage.uploaded_file_bucket`/`max_file_size`/`io_thread_pool_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`, `ingestion.cpu_thread_pool_size`/`download_concurrency`.

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
- `vector_store/types.py` — `VectorStoreType` (`qdrant`, `milvus`) and `SearchType` (`dense` only today). `SearchType` names the whole retrieval shape rather than exposing a retriever list plus a fusion strategy separately, because those two always have to agree — naming the combination keeps the invalid ones unrepresentable.
- `chunking/` — `ChunkingStrategy` enum, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Startup / bootstrap (`app/startup.py`)

Manual service-locator globals set by `init_embed_model`, `init_parsing_service`, `init_postgres`, `init_minio`, `init_vector_store`, `init_io_executor`, `init_cpu_executor`, `init_download_semaphore` and read via matching getters (`get_dense_embedding`, `get_dense_embedding_dim`, `get_parsing_service`, `get_postgres_pool`, `get_postgres_client`, `get_minio_service`, `get_vector_store_connection`, `get_io_executor`, `get_cpu_executor`, `get_download_semaphore`).

`init_embed_model` builds `EmbeddingService.from_settings()` for the configured provider and smoke-tests it; that same `check_connection()` call caches the vector dimension, read later via `get_dense_embedding_dim()` (which raises `RuntimeError` if called before init). `init_parsing_service` builds the providers once so a parsing backend's client is reused across files rather than rebuilt per ingestion. `init_vector_store` builds the connection for `VECTOR_STORE_PROVIDER`, verifies it, and registers it with `VectorStoreFactory`. `wait_for_postgres` retries the pool 5× at 0.5s and re-raises the last failure rather than continuing as if Postgres were reachable.

Used identically — but independently instantiated — by both `app/app.py` (web) and `app/tasks/broker.py` (worker), so there are no worker-local globals.

## Background worker (`app/tasks/`)

`broker.py` owns only the broker lifecycle: `RedisStreamBroker` + `RedisAsyncResultBackend` over `REDIS_URL`, with `WORKER_STARTUP`/`WORKER_SHUTDOWN` hooks that run `_initialize_services()` once (guarded by the `_initialized` flag) and close the Postgres pool plus `VectorStoreFactory.close_all()` on the way out. `_initialize_services()` mirrors `app.app`'s startup event but adds three things the web process does not need — `init_cpu_executor()`, `init_download_semaphore()`, and `init_parsing_service()` — because the worker is where the whole ingestion pipeline runs. Keeping the task in a separate module is what lets `app.tasks.broker:broker` be the deploy entrypoint without importing the ingestion pipeline just to start the process.

`ingestion_task.py` holds the one task, `ingest_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id, trace_context, vector_store_type)`. It is orchestration only: bind `request_id_ctx`, delegate to `IngestionService`, log and re-raise so TaskIQ sees the failure, reset the contextvar.

See [Flow](FLOW.md) for the full sequence diagrams.
