# Configuration Reference

Config is loaded from two sources, merged per-domain in `app/core/config/`:

1. **Environment variables / `.env`** (`app/core/config/settings.py`, pydantic-settings) — credentials, hosts, ports, and provider selection. Not version-controlled (`.env` is git-ignored; `.env.sample` documents the shape).
2. **`config/config.yaml`** (`YamlConfigLoader`, dot-notation `get(key, default)`) — stable, version-controlled tunables.

Required settings (no default — startup fails fast if missing/empty): `SERVING_API_KEY`, `FASTAPI_API_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT_URL`, `QDRANT_URL`, `QDRANT_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`.

## Provider selection

Four variables pick which backend serves each swappable capability. Each is validated by a `field_validator` in `settings.py`, so an unknown value fails at **startup**, not at first use.

| Variable | Default | Accepted values | Picks |
|---|---|---|---|
| `EMBEDDING_PROVIDER` | `openai` | `openai`, `tei` | `OpenAIEmbeddingProvider` (OpenAI-compatible endpoint at `VLLM_DENSE_EMBEDDING_URL`) or `TEIEmbeddingProvider` (raw HTTP to a Text Embeddings Inference `/embed` endpoint at `TEI_EMBEDDING_URL`) |
| `CHUNKING_PROVIDER` | `chonkie` | `chonkie`, `langchain` | `ChonkieProvider` or `LangchainProvider` (`langchain_text_splitters`) |
| `PDF_PARSER_PROVIDER` | `llamaparse` | `llamaparse` | PDF backend. Every other format (`.txt`, `.md`, `.docx`, `.doc`, images) always goes through the Unstructured API, so only PDF has a backend worth choosing |
| `VECTOR_STORE_PROVIDER` | `qdrant` | `qdrant`, `milvus` | Backend **new** vector stores are created on. Existing stores are read back using the provider recorded on their database row, so changing this doesn't strand old collections. Milvus is wired but every method raises `NotImplementedError` |

## Environment variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `FASTAPI_PORT` | 8005 | Web service port |
| `REDIS_PORT` | 6379 | Redis port |
| `POSTGRES_PORT` | 5432 | Postgres port |
| `MINIO_API_PORT` | 9000 | MinIO S3 API port |
| `MINIO_CONSOLE_PORT` | 9001 | MinIO web console port |
| `QDRANT_PORT` | 6333 | Qdrant HTTP port (6334 gRPC is fixed in compose, not env-configurable) |
| `SERVING_API_KEY` | — (required) | API key used when calling the embedding endpoint |
| `FASTAPI_API_KEY` | — (required) | Bearer token required on every `/v1/*` request (`Authorization: Bearer <value>`) |
| `UNDATASIO_API_KEY` | — | Unused — leftover from the removed UndatasIO parser |
| `LLAMAPARSE_API_KEY` | — | Required by `LlamaParseProvider` when a PDF is first parsed; used only when `PDF_PARSER_PROVIDER=llamaparse` |
| `UNSTRUCTURED_API_KEY` | — | Required by `UnstructuredProvider` when a non-PDF file is first parsed. Checked on first use, not at startup, so a PDF-only deployment can leave it empty |
| `UNSTRUCTURED_API_URL` | `https://api.unstructuredapp.io/general/v0/general` | Unstructured partition endpoint; point at a self-hosted instance to keep documents in-network |
| `PDF_PARSER_PROVIDER` | `llamaparse` | PDF parsing backend — see [Provider selection](#provider-selection) |
| `LOG_LEVEL` | `INFO` | `TRACE`\|`DEBUG`\|`INFO`\|`SUCCESS`\|`WARNING`\|`ERROR`\|`CRITICAL` |
| `LOG_FORMAT` | `auto` | `auto` (detect TTY) \| `console` (colorized) \| `json` (structured, for Docker/prod) |
| `DENSE_MODEL_NAME` | `Qwen/Qwen3-Embedding-0.6B` | Model name sent to the embedding endpoint |
| `VLLM_DENSE_EMBEDDING_URL` | `http://172.17.0.1:8100/v1` | OpenAI-compatible embedding endpoint; used only when `EMBEDDING_PROVIDER=openai` |
| `EMBEDDING_PROVIDER` | `openai` | Embedding backend — see [Provider selection](#provider-selection) |
| `TEI_EMBEDDING_URL` | `http://localhost:8100` | Base URL of the TEI embedding service; used only when `EMBEDDING_PROVIDER=tei` |
| `TEI_API_KEY` | — | Bearer token for the TEI embedding service; used only when `EMBEDDING_PROVIDER=tei` |
| `CHUNKING_PROVIDER` | `chonkie` | Chunking backend — see [Provider selection](#provider-selection) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_HOST` | — (required) | Postgres connection |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | — (required) | MinIO credentials |
| `MINIO_ENDPOINT_URL` | — (required) | MinIO endpoint, e.g. `http://minio:9000` |
| `VECTOR_STORE_PROVIDER` | `qdrant` | Vector database backend — see [Provider selection](#provider-selection) |
| `QDRANT_URL` | — (required) | Qdrant endpoint, e.g. `http://qdrant:6333` |
| `QDRANT_API_KEY` | — (required) | Qdrant API key |
| `MILVUS_URI` | `http://localhost:19530` | Milvus endpoint; unused until the Milvus backend is implemented |
| `MILVUS_TOKEN` | — | Milvus auth token; unused until the Milvus backend is implemented |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | — (required) | Langfuse OTLP Basic-Auth credentials |
| `LANGFUSE_BASE_URL` | — (required) | Self-hosted (or cloud) Langfuse base URL; traces post to `{this}/api/public/otel/v1/traces` |
| `API_VERSION` | `v1` | Router path prefix; must start with `v` |
| `NUM_WORKERS` | 1 | Uvicorn worker count (`--workers` in the web Compose command) |

## `config/config.yaml` (version-controlled)

| Key | Default | Description |
|---|---|---|
| `api.version` | `v1` | Informational; actual prefix is driven by `API_VERSION` env var |
| `api.num_workers` | 1 | Informational; actual worker count is driven by `NUM_WORKERS` env var |
| `storage.uploaded_file_bucket` | `uploaded-files` | MinIO bucket for uploaded file objects |
| `storage.max_file_size` | 100 | Max upload size in MB, enforced by `validate_file_size` |
| `storage.io_thread_pool_size` | 32 | Threads for blocking MinIO I/O (upload/download/delete), kept separate from the CPU pool |
| `redis.url` | `redis://redis:6379` | TaskIQ broker + result-backend connection string |
| `models.dense_model_name` | `Qwen/Qwen3-Embedding-0.6B` | Model name passed to the embedding backend |
| `embedding.upload_batch_size` | 16 | Chunks per batch in `EmbedAndIndexStage` — used for that batch's embed call and its upsert call alike |
| `embedding.batch_concurrency` | 4 | Batches in flight at once (embed + upsert combined) — peak memory stays roughly `upload_batch_size * batch_concurrency`, not file-sized |
| `ingestion.cpu_thread_pool_size` | 4 | Threads for CPU-bound chunking; sized to cores rather than to the I/O pool, since oversubscribing CPU work only adds context switching |
| `ingestion.download_concurrency` | 4 | Max files a worker process downloads from MinIO at once, so a burst of ingestion jobs can't exhaust the I/O pool |

Override the YAML file location via the `SETTINGS_YAML` mechanism in `YamlConfigLoader`, or edit `config/config.yaml` directly (it's mounted/copied into the image and version-controlled).

## Fixed constants (not environment-overridable)

Defined in `app/core/config/storage.py`, used by `app/api/dependencies.py`:

- `ALLOWED_MIME_TYPES` / `ALLOWED_EXTENSIONS`: `.pdf`, `.docx`, `.txt`, `.csv`, `.json`, `.jpg`/`.jpeg`, `.png`, `.gif` — upload-time acceptance list. It does not match the parsing registry in either direction: `.csv`, `.json`, and `.gif` upload fine but have no parsing provider, while `.md` and `.doc` are parseable but rejected at upload with a 415 (see `ParsingService` in [Detailed Components](en/DETAILED_COMPONENTS.md)).
- `MIME_TYPE_MAPPING`: cross-checked against the declared extension to reject MIME/extension mismatches with a 415.

## Where each setting is consumed

| Setting group | Read by |
|---|---|
| Provider switches | `EmbeddingService.from_settings()`, `ChunkingService.from_settings()`, `ParsingService.from_settings()`, `VectorStoreFactory.default_provider()` |
| Postgres / MinIO / vector store credentials | `app/startup.py` `init_*` functions, shared by the web app and the TaskIQ worker |
| `REDIS_URL` | `app/tasks/broker.py` — both the broker and the result backend |
| `EMBEDDING_UPLOAD_BATCH_SIZE` / `EMBEDDING_BATCH_CONCURRENCY` | `build_ingestion_pipeline()` → `EmbedAndIndexStage` |
| `IO_THREAD_POOL_SIZE` / `CPU_THREAD_POOL_SIZE` / `DOWNLOAD_CONCURRENCY` | `init_io_executor()` / `init_cpu_executor()` / `init_download_semaphore()` in `app/startup.py`; read back by `MinioFileStore`, the chunking providers, and `DownloadStage` |
| Langfuse credentials | `init_tracing()`, called independently by the web app and the worker |
