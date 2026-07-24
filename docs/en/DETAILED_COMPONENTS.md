# Detailed Components

## Router layer (`app/router/`)

### `file_router.py` — tag "File"

All endpoints require `Depends(verify_api_key)`.

| Method | Path | Handler | Notes |
|---|---|---|---|
| POST | `/v1/files` | `upload_file` | Form fields `purpose`, `file` (via `Depends(validate_file)`), `expires_after[anchor|seconds]` |
| GET | `/v1/files` | `list_files` | Query via `FileQueryRequest` (extends `PaginationParams`) |
| GET | `/v1/files/{file_id}` | `get_file_by_id` | — |
| DELETE | `/v1/files/{file_id}` | `delete_file` | Returns `FileDeletedResponse` |

### `vector_store_router.py` — tag "Vector Stores"

| Method | Path | Handler | Notes |
|---|---|---|---|
| POST | `/v1/vector_stores` | `create_vector_store` | Body `VectorStoreCreateRequest`; enqueues the ingest job |
| GET | `/v1/vector_stores` | `list_vector_stores` | Query `VectorStoreQueryRequest` |
| GET | `/v1/vector_stores/{id}` | `get_vector_store` | — |
| POST | `/v1/vector_stores/{id}` | `modify_vector_store` | OpenAI-style: update uses POST, not PATCH |
| DELETE | `/v1/vector_stores/{id}` | `delete_vector_store` | — |
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters`/`ranking_options` accepted but not yet applied |

`app/app.py` also defines `GET /health` (excluded from OpenAPI schema), registers `RequestIDMiddleware`, the global `AppBaseException` handler, and a startup event that brings up tracing → embed model → Postgres (pool + `wait_for_postgres` + table creation) → Qdrant → MinIO, in that order.

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Static methods `upload_file`, `list_files`, `get_file_by_id`, `delete_file`. Upload flow: generate `file_id`, build object path `{api_key}/uploads/{uuid}_{filename}`, upload bytes to MinIO, then insert the Postgres metadata row. If the Postgres insert fails after a successful MinIO upload, the object is left orphaned (logged as a warning, not cleaned up) and `PostgresConnectionException` is raised.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Static methods `create`, `list`, `get`, `modify`, `delete`, `search`. Composes `PostgresVectorStore` (metadata/status), `AsyncQdrantVectorStore` (vectors), TaskIQ (`process_vector_store_files.kiq(...)` enqueued from `create`), and `traced_span` for tracing. `search()` embeds the query text and calls `AsyncQdrantVectorStore.retrieve` directly — it does **not** go through the ingest pipeline; that path is TaskIQ-worker-only.

### Ingest pipeline (`services/ingest/`)

- `file_loader.py::load_and_chunk_file(minio_client, file_metadata, chunk_size)` — resolves a parser via `ParserFactory.get(file_ext)`, downloads bytes from MinIO, parses to text, chunks via `ChonkieChunkingService`.
- `ingest_pipeline.py::embed_and_upload_chunks` (and `embed_and_insert_batch[_bounded]`) — embeds chunk batches and upserts into `AsyncQdrantVectorStore.insert_documents`. Runs the first batch alone (avoids a collection-creation race), then remaining batches concurrently, bounded by `EMBEDDING_BATCH_CONCURRENCY`.

Composition: **Router → Service → (DB clients / TaskIQ enqueue) → Worker task → file_loader (parser + chunker) → ingest_pipeline (embed + upsert) → Qdrant**.

### Parsers (`services/parsers/`)

`BaseTextParser(ABC)` defines one abstract method: `async parse(file_bytes: bytes) -> str` — the interface is unified around raw bytes in, text out (the earlier UndatasIO-based parser has been fully removed).

`ParserFactory` registry, keyed by lowercased extension, instances cached per extension:

| Extension | Parser | Notes |
|---|---|---|
| `.txt`, `.md` | `AsyncTextParser` | Decodes bytes (`errors="ignore"`) in a thread pool |
| `.pdf` | `LlamaParseParser` | Wraps `llama_parse.LlamaParse` (Markdown output); writes to a temp file since LlamaParse needs a path |

Note the mismatch: upload-time `ALLOWED_EXTENSIONS` also allows `.docx`, `.csv`, `.json`, and images — those can be **uploaded** as Files but have no registered parser, so ingesting them raises `ValueError("Unsupported file format: ...")`.

### Chunking (`services/chunking/`)

`ChonkieChunkingService` (used by the ingest pipeline) — config `ChonkieChunkingConfig`, strategy enum `character | sentence | recursive | token` (default `recursive`, `chunk_size=800`, `chunk_overlap=400`). Maps to `chonkie.TokenChunker` / `SentenceChunker` / `RecursiveChunker` depending on strategy.

`LangchainChunkingService` also exists (wraps `langchain_text_splitters`) but is not wired into the current ingest path — legacy/alternate implementation.

At the API level, `VectorStoreCreateRequest.chunking_strategy` only exposes `"auto"` or `"static"` (with `max_chunk_size_tokens`/`chunk_overlap_tokens`) — there's no way to pick `sentence`/`token`/`character` through the public API even though the underlying chunker supports them. Also, `chunk_overlap` is computed in `VectorStoreService.create` but not actually passed into the enqueued task (only `chunk_size` is) — a known gap, see [Design Decisions](DESIGN_DECISIONS.md).

## Data layer (`app/db/`)

| Package | Class(es) | Backs | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Uploaded file bytes (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | `files` + `vector_stores` metadata tables (ownership by `api_key`, status, JSONB metadata) | `postgres` (5432) |
| `qdrant/` | `QdrantService`, `AsyncQdrantVectorStore` | One Qdrant collection per vector store (`collection_name == vector_store_id`) | `qdrant` (6333/6334) |

`AsyncQdrantVectorStore` lazily creates its collection on first insert (HNSW + configurable quantization — `scalar`/`binary`/`product` — with `indexing_threshold=0` at creation, raised to `20000` after bulk insert to avoid indexing overhead mid-load). `retrieve` runs one `query_points` call per query vector, gathered in parallel.

## Configuration (`app/core/config/`)

Two layers merged per domain module:

1. **pydantic-settings** (`settings.py`) — reads `.env` / real environment variables. Required fields (no default) include API keys, Postgres/MinIO/Qdrant/Langfuse credentials. Validators enforce `API_VERSION` starts with `v`, ports are positive, secrets are non-empty, `LOG_LEVEL`/`LOG_FORMAT` are in an allowed set.
2. **YAML** (`config/config.yaml`, loaded via `YamlConfigLoader`) — stable tunables: `api.num_workers`, `storage.uploaded_file_bucket`/`max_file_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`.

Per-domain modules (`database.py`, `storage.py`, `models.py`, `embedding.py`, `redis.py`, `langfuse.py`, `api.py`) combine both sources into flat constants importable from `app.core.config`. Full parameter list: [Configuration Reference](../CONFIGURATION.md).

## Tracing (`app/core/tracing/`)

Langfuse via **OpenTelemetry OTLP** exporter — `init_tracing()` builds a `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter(...))` pointed at `{LANGFUSE_BASE_URL}/api/public/otel/v1/traces` with Basic-Auth from the public/secret key pair. `traced_span(name, attributes)` is a context manager used throughout the search and ingest paths; it auto-sets `Status.OK`/`Status.ERROR` and records exceptions.

- **Search**: outer span tagged `vector_store_search`, nested `embedding` and `retrieve` spans (the latter deliberately excludes chunk payload/content from tracing, only `chunk_id`/`score`).
- **Ingest**: outer span tagged `vector_store_ingest`, nested `embedding` and `upsert` spans.

Both the web app (`app.py`) and the TaskIQ worker (`taskiq_worker.py`) call `init_tracing()` independently at their own startup.

## Request correlation (`app/middleware/request_id.py`)

`RequestIDMiddleware` is a raw ASGI middleware (not `BaseHTTPMiddleware` — that would reset the contextvar before uvicorn's access logger fires). It reuses a client-supplied `X-Request-Id` header if present, else generates `req_{uuid4().hex}`, binds it to `request_id_ctx` (a `contextvars.ContextVar` in `app/core/request_context.py`) for the request's duration, and echoes it back on the response. The same ID is passed into `process_vector_store_files.kiq(...)` and re-bound inside the worker task, so a single request ID threads through HTTP logs and the async ingest job that request triggered.

## Security (`app/security/auth.py`)

`verify_api_key` requires an `Authorization: Bearer <token>` header, compares the token against the single configured `FASTAPI_API_KEY`, and returns the token as `api_key` — which is then used to scope Postgres rows. Because there is exactly one valid token, this is effectively single-tenant auth even though the schema is shaped for multi-tenant ownership (see [Design Decisions](DESIGN_DECISIONS.md)).

## Exceptions (`app/exceptions/`)

Root `AppBaseException(status_code, response: BaseResponse, log_message)`. Domain subclasses: `auth/` (`APIKeyIncorrectException`, `BearerMissingException` — 401), `file/` (`FileSizeLimitExceededException` 413, `FileNotFoundException` 404), `postgres/` (`PostgresConnectionException` 503), `vector_store/` (`VectorStoreNotFoundException` 404, `WrongPrefixVectorstoreException` 400). `common_exception_handler` (registered globally) logs at ERROR/WARNING depending on status and returns `JSONResponse({message, type, params, code})` — the OpenAI-style error envelope. Plain `HTTPException`s (e.g. 415 from `dependencies/file_validation.py`) fall through to FastAPI's default handler instead.

## Schemas (`app/schemas/`)

- `base/` — shared `BaseModel` (`extra="forbid"`), `PaginationParams`, generic `PaginatedResponse[T]`.
- `file/` — `FileObject`, `FileListObject`, request/response variants, `UploadingStatus` enum.
- `vector_store/` — `VectorStoreCreateRequest`/`ModifyRequest`/`QueryRequest`, chunking-strategy union types, filter/ranking types (defined, not yet applied), `VectorStoreObject`, `VectorStoreSearchResponse`.
- `chunking/` — `ChunkingStrategy` enum, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Dependencies (`app/dependencies/file_validation.py`)

`validate_file_size` (rejects over `MAX_FILE_SIZE` MB), `validate_file_type` (checks MIME + extension + their consistency via `MIME_TYPE_MAPPING`), composed as `validate_file` — used as `Depends(validate_file)` on the upload endpoint.

## Startup / bootstrap (`app/startup/startup.py`)

Manual service-locator globals (`embed_model`, `postgres_client`, `minio_service`, `qdrant_service`) set by `init_embed_model`/`init_postgres`/`init_minio`/`init_qdrant` and read via matching getters. `init_embed_model` creates an `AsyncOpenAI` client against `VLLM_DENSE_EMBEDDING_URL` (embeddings are served through an OpenAI-compatible vLLM endpoint, not a local model) and smoke-tests it. Used identically — but independently instantiated — by both `app/app.py` (web) and `taskiq_worker.py` (worker).

## Background worker (`taskiq_worker.py`)

`RedisStreamBroker` + `RedisAsyncResultBackend`, both over `REDIS_URL`. `WORKER_STARTUP`/`WORKER_SHUTDOWN` hooks lazily initialize/tear down the same services as the web app. The one task, `process_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id)`:

1. Filters `file_ids` against Postgres (`check_existing_files`), fetches total bytes + metadata
2. **Processes exactly one file** — if `file_ids` resolves to more than one file, the extra files are silently skipped (chunked_texts stays empty) and the vector store is still marked `completed`
3. Loads + chunks the (single) file, embeds + upserts into Qdrant
4. Marks the vector store `failed` on any error, else `completed` with `usage_bytes`

See [Flow](FLOW.md) for the full sequence diagrams.
