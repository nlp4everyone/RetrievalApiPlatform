# RetrievalApiPlatform — OpenAI-compatible Files & Vector Stores Retrieval Engine

Production-oriented backend implementing OpenAI's **Files** and **Vector Stores** resource model for RAG systems, using:

- FastAPI (`app/app.py`) as the API layer — `file_router` (`/v1/files`) and `vector_store_router` (`/v1/vector_stores`), with the whole HTTP boundary living in `app/api/`
- PostgreSQL (`asyncpg`) for file/vector store metadata
- MinIO as object storage for the raw bytes of uploaded files
- A pluggable vector database behind `BaseAsyncVectorStore` — one collection per vector store. Qdrant is implemented; Milvus is wired but stubbed
- Redis Streams + TaskIQ for asynchronous ingestion, running outside the request/response lifecycle
- Two external parsing services: LlamaParse for `.pdf`, the Unstructured API for every other format — both return Markdown
- An external dense embedding endpoint (OpenAI-compatible vLLM, or Text Embeddings Inference) for actual vector computation
- Langfuse (via OpenTelemetry OTLP) for end-to-end tracing across upload/ingestion/search

---

# Architecture

## Layers

The import graph runs strictly downwards — `app.components` and `app.pipelines` never reach up into `app.api`, which is what lets the TaskIQ worker run without FastAPI installed in its code path.

```
app/api          HTTP boundary        routers, Depends validation, auth, middleware
app/services     business logic       FileService, VectorStoreService, IngestionService
app/pipelines    orchestration        Pipeline + BaseStage; ingestion & retrieval
app/components   capabilities         parsing, chunking, embedding (provider-backed)
app/db           persistence          postgres, minio, vector_store (provider-backed)
app/core         cross-cutting        config, tracing, request context
```

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
│                          — all from app/api/ —                           │
└───┬────────────────────────────────────┬─────────────────────────────────┘
    │                                    │
    ▼                                    ▼
file_router                       vector_store_router
/v1/files                         /v1/vector_stores{,/search}
    │                                    │
    ▼                                    ▼
FileService                       VectorStoreService
    │                                    │
    ▼                         ┌──────────┴───────────────┐
PostgresFileStore             │                          │
MinioFileStore                ▼                          ▼
                        create()                     search()
              status=in_progress, responds       RetrievalPipeline
                     RIGHT AWAY                   (in-process)
                          │                          │
                          ▼                    embed_query → retrieve → fuse
        ingest_vector_store_files.kiq(...)           │
        + inject_trace_context()  ──────────▶  DenseRetriever
        (pushes onto a Redis Stream)                 │
                          │                          ▼
                          ▼                  VectorStoreFactory.get_store()
       ┌──────────────────────────────────┐          │
       │  TaskIQ Worker (own container)   │          ▼
       │  app.tasks.broker:broker         │   BaseAsyncVectorStore
       │  app.tasks.ingestion_task        │   (Qdrant | Milvus*)
       └────────────────┬─────────────────┘
                        ▼
              IngestionService.ingest_vector_store_files()
                        │
                        ▼
              IngestionPipeline (app/pipelines/ingestion)

      download  ──▶  parse    ──▶  chunk    ──▶  embed_index
      MinIO          Parsing       Chunking      EmbedAndIndexStage
      semaphore      Service       Service       per batch (16 chunks):
      + I/O pool     LlamaParse    CPU pool        embed → Documents → upsert
                     (.pdf)                        holding one semaphore(4) slot
                     Unstructured                ensure_collection() exactly once,
                     (the rest)                  embedding_dim from startup cache
                        │
                        ▼
        PostgresVectorStore.update(status=completed | failed)

  Pipeline.run() opens one span per stage ──▶ Langfuse (OTel OTLP)
  trace_context travels with the task, so worker spans join the request's trace

  * Milvus is registered end-to-end but every method raises NotImplementedError
```

## Overall request flow

**Step 1 — Auth + validate**

Every route (except `/health`) depends on `verify_api_key` (`app/api/security.py`), which checks the `Authorization: Bearer <FASTAPI_API_KEY>` header against a static key. File upload goes through the `validate_file` dependency (`app/api/dependencies.py` — MIME/extension match + size check) before reaching the handler; the `vector_store_id` path parameter is checked for the correct `vs` prefix before any lookup.

**Step 2 — Upload file**

`POST /v1/files` — `FileService.upload_file` generates a `file_id` (`file-{8 hex}`), uploads the bytes to MinIO, then inserts a metadata row into Postgres, and returns the `FileObject` right away. The file is **not** parsed/chunked/embedded at upload time — that only happens once the file is attached to a vector store.

**Step 3 — Create vector store → asynchronous ingestion**

`POST /v1/vector_stores {name, file_ids, chunking_strategy}` — `VectorStoreService.create` rejects more than one `file_id` up front (`UnsupportedMultipleFilesException`, 400), creates a Postgres row with `status=in_progress` and the provider it was created with, enqueues `ingest_vector_store_files.kiq(...)` onto a Redis Stream along with the current W3C trace context, and returns the `VectorStoreObject` immediately — without waiting for ingestion to finish.

**Step 4 — Background ingestion (TaskIQ worker)**

`ingest_vector_store_files` (`app/tasks/ingestion_task.py`) is a thin adapter: it binds the correlation id and delegates to `IngestionService.ingest_vector_store_files`. That service resolves which files still exist, builds an `IngestionPipeline` via `build_ingestion_pipeline(...)`, and runs it against an `IngestionContext`. The pipeline's four stages — `download`, `parse`, `chunk`, `embed_index` — each read from and write to that one context object. Any failure flips `status=failed` and re-raises; success sets `status=completed` with `usage_bytes`.

The last step is **one** streaming stage (`EmbedAndIndexStage`), not two: each batch of chunks is embedded and upserted while holding the same semaphore slot, so writes begin with the first batch instead of after the whole file is embedded, and peak memory is bounded by `batch_size × concurrency` rather than file size. The collection is created before the loop from the vector dimension cached at startup (`get_dense_embedding_dim()`) — that is what makes streaming possible, since there is no longer any need to wait for the first embedding result to learn `embedding_dim`.

The worker process runs **two** separate thread pools plus a download cap: the I/O pool (`IO_THREAD_POOL_SIZE`) for MinIO transfers, the CPU pool (`CPU_THREAD_POOL_SIZE`) for chunking, and `asyncio.Semaphore(DOWNLOAD_CONCURRENCY)` limiting concurrent downloads. Sharing a single pool would make CPU-bound chunking queue behind slow transfers, and a burst of concurrent ingestion jobs could exhaust the pool on its own.

**Step 5 — Search**

`POST /v1/vector_stores/{id}/search {query, max_num_results, filters}` — `VectorStoreService.search` reads the vector store row (for its `vector_store_type`), normalises `filters` into the backend-neutral tree, and runs a `RetrievalPipeline` in-process: `embed_query → retrieve → fuse`. `DenseRetriever` returns `[]` rather than erroring when the collection does not exist yet (ingestion still running, or it failed before the collection was created).

> For exact function names, variable names, and gate-by-gate logic: [FLOW.md](FLOW.md)

---

# Pipelines

Both pipelines are the same machinery (`app/pipelines/pipeline.py`) with different stages. `Pipeline.run()` is the **only** place that opens spans: stages contain business logic, declare a `name`, and report metrics from `span_attributes()`. A stage can return `False` from `emits_span()` to stay out of the trace on a run where it was a no-op — it still executes.

| | Ingestion | Retrieval |
|---|---|---|
| Runs in | TaskIQ worker | web process, inside the request |
| Context | `IngestionContext` | `RetrievalContext` |
| Stages | `download → parse → chunk → embed_index` | `embed_query → retrieve → fuse` |
| Assembled by | `build_ingestion_pipeline(...)` | `build_retrieval_pipeline(..., search_type)` |
| Parent trace | `trace_context` carried through the task | the ambient request span |

Adding a step is adding a `BaseStage` subclass and one line in the factory. Adding hybrid search is adding a `BaseRetriever` and a `BaseFusion`, plus a branch in `_build_plan()` — the stages and the pipeline stay untouched.

---

# HTTP Endpoints

Every route is prefixed with `/v1` and requires `Authorization: Bearer <FASTAPI_API_KEY>`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/files` | Upload a file (multipart: `purpose`, `file`, `expires_after`) |
| `GET` | `/files` | List files (paginated, filterable by `purpose`) |
| `GET` | `/files/{file_id}` | Retrieve a file |
| `DELETE` | `/files/{file_id}` | Delete a file |
| `POST` | `/vector_stores` | Create a vector store (one `file_id` → triggers background ingestion; more than one → 400) |
| `GET` | `/vector_stores` | List vector stores (cursor-paginated) |
| `GET` | `/vector_stores/{vector_store_id}` | Retrieve a vector store, including `status`/`file_counts` |
| `POST` | `/vector_stores/{vector_store_id}` | Modify a vector store (OpenAI-style: uses `POST`, not `PATCH`) |
| `DELETE` | `/vector_stores/{vector_store_id}` | Delete a vector store |
| `POST` | `/vector_stores/{vector_store_id}/search` | Search by `query`, `max_num_results`, `filters` (`ranking_options` accepted but not yet applied) |

Both routers speak the OpenAI object model (`FileObject`, `VectorStoreObject`, paginated `object="list"` responses), OpenAI's ID convention (`file-{8 hex}`, `vs-{32 hex}` — `app/utils/key_generator/key_generator.py`), and an OpenAI-style error envelope (`{message, type, params, code}`) on every `AppBaseException`.

Not yet implemented: multi-file ingestion for a single vector store, a vector-store-file sub-resource endpoint (attach/list/detach), parsers for `.csv`/`.json`/`.gif` (uploadable but unmapped), chunk-level ingestion progress/cancellation. See [README.md](README.md#to-do--roadmap).

---

# Background Worker

| | |
|---|---|
| Queue | Redis Streams (`RedisStreamBroker`, TaskIQ) |
| Entrypoint | `taskiq worker app.tasks.broker:broker` |
| Task | `ingest_vector_store_files` (`app/tasks/ingestion_task.py`) |
| Business logic | `IngestionService` (`app/services/ingestion/`) — no TaskIQ import, so it is callable and testable without a broker |
| Container | `taskiq_worker`, `restart: always`; depends on `postgres`, `redis`, `minio` being healthy (`compose_web.yml`) |
| Processing limit | Exactly **one** file per vector store. `VectorStoreService.create` rejects more at request time; `IngestionService` re-checks and marks the store `failed` rather than reporting `completed` on an empty store |
| Batching | `EMBEDDING_UPLOAD_BATCH_SIZE=16` chunks per batch, used for that batch's embed call and its upsert call alike; the whole loop is bounded by **one** `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)` |
| Streaming | `EmbedAndIndexStage` embeds then upserts each batch while holding the same semaphore slot. `Document` objects are built **after** the slot is acquired, so at most `concurrency` batches' worth of chunks/vectors/Documents exist at once instead of the whole file's |
| Collection creation | `ensure_collection(embedding_dim)` is called exactly once before the loop, with `embedding_dim` from `get_dense_embedding_dim()` (cached at startup by `EmbeddingService.check_connection()`), so every insert is a pure write and they all run concurrently |
| Thread pools | I/O pool (`IO_THREAD_POOL_SIZE=32`) for MinIO, kept apart from the CPU pool (`CPU_THREAD_POOL_SIZE=4`) for chunking; `MinioFileStore._fetch_object` folds `get_object()` + `.read()` into a single offloaded call, since opening the stream is just headers while the real transfer is `.read()` |
| Download cap | `asyncio.Semaphore(DOWNLOAD_CONCURRENCY=4)` per worker process, so one burst of ingestion jobs cannot exhaust the I/O pool on its own |
| Correlation | `request_id_ctx` is re-bound inside the worker from the `request_id` passed through `.kiq(...)`; `trace_context` (W3C) is extracted so ingestion spans nest inside the originating request's Langfuse trace |

`app/tasks/broker.py` owns only the broker lifecycle — connect, bootstrap services on `WORKER_STARTUP`, close them on `WORKER_SHUTDOWN`. Keeping the task in a separate module is what lets the broker be the deploy entrypoint without importing the ingestion pipeline just to start the process.

---

# Repository structure

```
app/
  app.py                  # FastAPI app: middleware, routers, exception handler, startup event
  startup.py              # init_*/get_* service locator, shared by web and worker
                          # (including the I/O pool, CPU pool, and download semaphore)
  api/                    # everything that exists only because this is served over HTTP
    router/               # file_router.py, vector_store_router.py
    dependencies.py       # validate_file (size + MIME/extension checks)
    security.py           # verify_api_key (Bearer, single static key)
    middleware.py         # RequestIDMiddleware (raw ASGI, not BaseHTTPMiddleware)
  services/
    file/                 # FileService — upload/list/get/delete (MinIO + Postgres)
    vector_store/         # VectorStoreService — create/list/get/modify/delete/search
    ingestion/            # IngestionService — the background task's business logic
  pipelines/
    base.py, pipeline.py  # BaseStage contract + the runner that owns all tracing
    ingestion/            # context, factory, pipeline, stages/ (download→parse→chunk→embed_index)
    retrieval/            # context, factory, pipeline, fusion, retriever/, stages/
  components/             # swappable capabilities: base.py + provider/ + <X>Service.from_settings()
    parsing/              # LlamaParseProvider (.pdf), UnstructuredProvider (every other format)
    chunking/             # ChonkieProvider, LangchainProvider
    embedding/            # OpenAIEmbeddingProvider, TEIEmbeddingProvider
  db/
    minio/                # MinioService, MinioFileStore — object storage
    postgres/             # PostgresClient, PostgresFileStore, PostgresVectorStore, schema/
    vector_store/         # base.py, types.py, factory.py + provider/qdrant, provider/milvus
  core/
    config/               # settings.py (.env) + per-domain modules merging YAML + env
    tracing/              # init_tracing(), traced_span(), trace-context propagation, attributes
    request_context.py    # request_id_ctx ContextVar
  schemas/                # base/, file/, vector_store/, chunking/ — Pydantic request/response models
  exceptions/             # AppBaseException hierarchy + common_exception_handler
  tasks/
    broker.py             # RedisStreamBroker + worker startup/shutdown (deploy entrypoint)
    ingestion_task.py     # ingest_vector_store_files — thin adapter over IngestionService
  utils/                  # config_loader, datetime_utils, io, key_generator, vector_store helpers
config/config.yaml        # version-controlled tunables: embedding batch size/concurrency,
                          # bucket name, storage.io_thread_pool_size,
                          # ingestion.cpu_thread_pool_size, ingestion.download_concurrency
docker/                   # Dockerfile + compose_db.yml / compose_web.yml / compose_tracking.yml
examples/file_upload_example.py  # end-to-end demo using the openai SDK
```

---

# Docker Compose topology

Three compose files combined by the `Makefile`:

- **`compose_db.yml`** — `postgres` (5432), `redis` (6379), `qdrant` (6333 HTTP / 6334 gRPC)
- **`compose_tracking.yml`** — `minio` (9000 API / 9001 console) — object storage, despite the filename
- **`compose_web.yml`** — `web` (uvicorn, 8005; depends on `postgres` healthy + `worker` started) and `worker` (TaskIQ; depends on `postgres`, `redis`, `minio` healthy)

Langfuse itself is **not** part of this Compose stack — configuration points at an external/self-hosted instance. Same for the embedding server — `DENSE_EMBEDDING_URL` defaults to `http://172.17.0.1:8100/v1` (the host machine, not a Compose service).

Both `web` and `worker` run from the same image (`docker/Dockerfile`, multi-stage `uv sync --frozen` build, non-root `appuser`), just with different entrypoint commands (`uvicorn app.app:app` vs `taskiq worker app.tasks.broker:broker`). Each independently runs the same bootstrap out of `app/startup.py`, so both processes end up with the same live services reached through the same getters.

---

# Configuration

See [CONFIGURATION.md](../CONFIGURATION.md) for the full parameter table: `.env` (infra/secrets/provider switches) vs. `config/config.yaml` (static tunables) vs. defaults in code.

---

> Layer-by-layer breakdown and API reference: [DETAILED_COMPONENTS.md](DETAILED_COMPONENTS.md)
