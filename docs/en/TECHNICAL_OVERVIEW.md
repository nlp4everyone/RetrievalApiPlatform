# RetrievalApiPlatform — OpenAI-compatible Files & Vector Stores Retrieval Engine

Production-oriented backend implementing OpenAI's **Files** and **Vector Stores** resource model for RAG systems, using:

- FastAPI (`app/app.py`) as the API layer — `file_router` (`/v1/files`) and `vector_store_router` (`/v1/vector_stores`)
- PostgreSQL (`asyncpg`) for file/vector store metadata
- MinIO as object storage for the raw bytes of uploaded files
- Qdrant as the vector database — one collection per vector store
- Redis Streams + TaskIQ for asynchronous ingest (parse → chunk → embed → upsert), running outside the request/response lifecycle
- An external OpenAI-compatible embedding endpoint (vLLM serving `Qwen/Qwen3-Embedding-0.6B`) for actual vector computation
- Langfuse (via OpenTelemetry OTLP) for end-to-end tracing across upload/ingest/search

---

# Architecture

## Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    CLIENT (openai Python SDK / HTTP)                     │
│      client.files.* / client.vector_stores.* ... / Bearer token          │
└────────────────────────────────┬─────────────────────────────────────────┘
                                  │ HTTP  /v1/...
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Application  (app/app.py)                     │
│  RequestIDMiddleware · verify_api_key (Bearer) · validate_file · routers │
└───┬────────────────────────────────────┬─────────────────────────────────┘
    │                                    │
    ▼                                    ▼
file_router                       vector_store_router
/v1/files                         /v1/vector_stores{,/search}
    │                                    │
    ▼                                    ▼
FileService                       VectorStoreService
    │                                    │
    ▼                                    ▼
PostgresFileStore · MinioFileStore       PostgresVectorStore · AsyncQdrantVectorStore
                                          │
                                          │ create() → status=in_progress, responds RIGHT AWAY
                                          ▼
                             process_vector_store_files.kiq(...)
                             (pushes the ingest job onto a Redis Stream)
                                          │
                                          ▼
                    ┌─────────────────────────────────────────┐
                    │   TaskIQ Worker (separate container,      │
                    │   taskiq_worker.py)                      │
                    │   process_vector_store_files             │
                    └────────────────────┬──────────────────────┘
                                          ▼
                  load_and_chunk_file()  ──▶  embed_and_upload_chunks()
                  (MinIO download + parser + Chonkie)   (embedding + Qdrant upsert)
                                          │
                          ┌───────────────┴────────────────┐
                          ▼                                 ▼
              External embedding endpoint            AsyncQdrantVectorStore
              (vLLM, OpenAI-compatible)                .insert_documents()
                          │                                 │
                          └───────────────┬─────────────────┘
                                          ▼
                        PostgresVectorStore.update(status=completed | failed)

      Every step (upload · create · search · ingest) ──▶ traced_span() ──▶ Langfuse (OTel OTLP)
```

## Overall request flow

**Step 1 — Auth + validate**

Every route (except `/health`) depends on `verify_api_key`, which checks the `Authorization: Bearer <FASTAPI_API_KEY>` header against a static key. File upload goes through the `validate_file` dependency (MIME/extension match + size check) before reaching the handler; the `vector_store_id` path parameter is checked for the correct `vs` prefix before any lookup.

**Step 2 — Upload file**

`POST /v1/files` — `FileService.upload_file` generates a `file_id` (`file-{8 hex}`), uploads the bytes to MinIO, then inserts a metadata row into Postgres, and returns the `FileObject` right away. The file is **not** parsed/chunked/embedded at upload time — that only happens once the file is attached to a vector store.

**Step 3 — Create vector store → asynchronous ingest**

`POST /v1/vector_stores {name, file_ids, chunking_strategy}` — `VectorStoreService.create` creates a Postgres row with `status=in_progress`, enqueues the `process_vector_store_files.kiq(...)` task onto a Redis Stream, and returns the `VectorStoreObject` immediately — without waiting for ingest to finish. Vector generation happens afterwards, on a separate worker process.

**Step 4 — Background ingest (TaskIQ worker)**

`process_vector_store_files` runs inside `taskiq_worker.py`: `check_existing_files` skips ingest if every referenced file has been deleted; otherwise `load_and_chunk_file` downloads the bytes from MinIO, picks a parser by extension (`ParserFactory`), and chunks the text with Chonkie; `embed_and_upload_chunks` embeds each batch (`EMBEDDING_UPLOAD_BATCH_SIZE=16`) and upserts into Qdrant — the first batch runs alone to create the collection first and avoid a race, the remaining batches run concurrently bounded by `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)`. Any failure along the way flips `status=failed`; success sets `status=completed`.

**Step 5 — Search**

`POST /v1/vector_stores/{id}/search {query, max_num_results}` — `VectorStoreService.search` embeds the query and calls `AsyncQdrantVectorStore.retrieve` directly (it does not go through the ingest pipeline). If the Qdrant collection doesn't exist yet (e.g. ingest still running or it failed before the collection was created), it returns `data=[]` instead of erroring.

> For exact function names, variable names, and gate-by-gate logic: [FLOW.md](FLOW.md)

---

# HTTP Endpoints

Every route is prefixed with `/v1` and requires `Authorization: Bearer <FASTAPI_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/files` | Upload a file (multipart: `purpose`, `file`, `expires_after`) |
| `GET` | `/files` | List files (paginated, filterable by `purpose`) |
| `GET` | `/files/{file_id}` | Retrieve a file |
| `DELETE` | `/files/{file_id}` | Delete a file |
| `POST` | `/vector_stores` | Create a vector store (with `file_ids` → triggers background ingest) |
| `GET` | `/vector_stores` | List vector stores (cursor-paginated) |
| `GET` | `/vector_stores/{vector_store_id}` | Retrieve a vector store, including `status`/`file_counts` |
| `POST` | `/vector_stores/{vector_store_id}` | Modify a vector store (OpenAI-style: uses `POST`, not `PATCH`) |
| `DELETE` | `/vector_stores/{vector_store_id}` | Delete a vector store |
| `POST` | `/vector_stores/{vector_store_id}/search` | Search by `query`, `max_num_results` (`filters`/`ranking_options` are accepted but not yet applied) |

Both routers speak the OpenAI object model (`FileObject`, `VectorStoreObject`, paginated `object="list"` responses), OpenAI's ID convention (`file-{8 hex}`, `vs-{32 hex}` — `app/utils/key_generator/key_generator.py`), and an OpenAI-style error envelope (`{message, type, params, code}`) on every `AppBaseException`.

Not yet implemented: multi-file ingest for a single vector store, a vector-store-file sub-resource endpoint (attach/list/detach), a `.docx` parser, chunk-level ingest progress/cancellation. See [README.md](README.md#to-do--roadmap).

---

# Background Worker

| | |
|---|---|
| Queue | Redis Streams (`RedisStreamBroker`, TaskIQ) |
| Task | `process_vector_store_files` (`taskiq_worker.py`) |
| Container | `taskiq_worker`, `restart: always`; depends on `postgres`, `redis`, `minio` being healthy (`compose_web.yml`) |
| Processing limit | Only supports exactly **one** file per vector store today — extra/multiple files are silently skipped (multi-file ingest is a TODO) |
| Embedding batching | `EMBEDDING_UPLOAD_BATCH_SIZE=16`; concurrency bounded by `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)`; the first batch runs alone to avoid a race when creating the Qdrant collection |
| Idempotency / failure | `check_existing_files` skips load/chunk/embed if all files were deleted; any failure along the way → `_mark_failed` (`status=failed`) → re-raise |
| Correlation | `request_id_ctx` is re-bound inside the worker from the `request_id` passed through `.kiq(...)`, so worker logs correlate back to the originating HTTP request |

---

# Repository structure

```
app/
  app.py                  # FastAPI app: middleware, routers, exception handler, startup event
  router/                 # file_router.py, vector_store_router.py
  schemas/                # base/, file/, vector_store/, chunking/ — Pydantic request/response models
  services/
    file/                 # FileService — upload/list/get/delete (MinIO + Postgres)
    vector_store/         # VectorStoreService — create/list/get/modify/delete/search
    ingest/                # file_loader.py (parse+chunk), ingest_pipeline.py (embed+upsert)
    parsers/               # BaseTextParser, AsyncTextParser, LlamaParseParser, ParserFactory
    chunking/              # ChonkieChunkingService (used), LangchainChunkingService (unused by ingest)
  db/
    minio/                 # MinioService, MinioFileStore — object storage
    postgres/              # PostgresClient, PostgresFileStore, PostgresVectorStore, schema/
    qdrant/                # QdrantService, AsyncQdrantVectorStore
  core/
    config/                # settings.py (.env) + per-domain modules merging YAML + env
    tracing/               # init_tracing(), traced_span() — Langfuse via OTel OTLP
    request_context.py     # request_id_ctx ContextVar
  security/auth.py         # verify_api_key (Bearer, single static key)
  middleware/request_id.py # RequestIDMiddleware (raw ASGI, not BaseHTTPMiddleware)
  exceptions/              # AppBaseException hierarchy + common_exception_handler
  dependencies/file_validation.py  # validate_file (size + MIME/extension checks)
  startup/startup.py       # init_embed_model/init_postgres/init_minio/init_qdrant + getters
  utils/                   # config_loader, datetime_utils, io, key_generator, tracing helpers
taskiq_worker.py           # RedisStreamBroker + process_vector_store_files task
config/config.yaml         # version-controlled tunables (batch sizes, bucket name, ...)
docker/                    # Dockerfile + compose_db.yml / compose_web.yml / compose_tracking.yml
examples/file_upload_example.py  # end-to-end demo using the openai SDK
```

---

# Docker Compose topology

Three compose files combined by the `Makefile`:

- **`compose_db.yml`** — `postgres` (5432), `redis` (6379), `qdrant` (6333 HTTP / 6334 gRPC)
- **`compose_tracking.yml`** — `minio` (9000 API / 9001 console) — object storage, despite the filename
- **`compose_web.yml`** — `web` (uvicorn, 8005; depends on `postgres` healthy + `worker` started) and `worker` (TaskIQ; depends on `postgres`, `redis`, `minio` healthy)

Langfuse itself is **not** part of this Compose stack — configuration points at an external/self-hosted instance. Same for the embedding server — `VLLM_DENSE_EMBEDDING_URL` defaults to `http://172.17.0.1:8100/v1` (the host machine, not a Compose service).

Both `web` and `worker` run from the same image (`docker/Dockerfile`, multi-stage `uv sync --frozen` build, non-root `appuser`), just with different entrypoint commands (`uvicorn app.app:app` vs `taskiq worker taskiq_worker:broker`) — each independently runs the same `startup`/`initialize_services` bootstrap.

---

# Configuration

See [CONFIGURATION.md](../CONFIGURATION.md) for the full parameter table: `.env` (infra/secrets) vs. `config/config.yaml` (static tunables) vs. defaults in code.

---

> Layer-by-layer breakdown and API reference: [DETAILED_COMPONENTS.md](DETAILED_COMPONENTS.md)
