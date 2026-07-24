# Configuration Reference

Config is loaded from two sources, merged per-domain in `app/core/config/`:

1. **Environment variables / `.env`** (`app/core/config/settings.py`, pydantic-settings) — credentials, hosts, ports. Not version-controlled (`.env` is git-ignored; `.env.sample` documents the shape).
2. **`config/config.yaml`** (`YamlConfigLoader`, dot-notation `get(key, default)`) — stable, version-controlled tunables.

Required settings (no default — startup fails fast if missing/empty): `SERVING_API_KEY`, `FASTAPI_API_KEY`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT_URL`, `QDRANT_URL`, `QDRANT_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`.

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
| `LLAMAPARSE_API_KEY` | — | Required at import time by `LlamaParseParser` if PDF ingest is used |
| `LOG_LEVEL` | `INFO` | `TRACE`\|`DEBUG`\|`INFO`\|`SUCCESS`\|`WARNING`\|`ERROR`\|`CRITICAL` |
| `LOG_FORMAT` | `auto` | `auto` (detect TTY) \| `console` (colorized) \| `json` (structured, for Docker/prod) |
| `VLLM_DENSE_EMBEDDING_URL` | `http://172.17.0.1:8100/v1` | OpenAI-compatible embedding endpoint (e.g. vLLM serving the dense model) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_HOST` | — (required) | Postgres connection |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | — (required) | MinIO credentials |
| `MINIO_ENDPOINT_URL` | — (required) | MinIO endpoint, e.g. `http://minio:9000` |
| `QDRANT_URL` | — (required) | Qdrant endpoint, e.g. `http://qdrant:6333` |
| `QDRANT_API_KEY` | — (required) | Qdrant API key |
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
| `redis.url` | `redis://redis:6379` | TaskIQ broker + result-backend connection string |
| `models.dense_model_name` | `Qwen/Qwen3-Embedding-0.6B` | Model name passed to the embedding endpoint's `embeddings.create` |
| `embedding.upload_batch_size` | 16 | Chunks embedded/upserted per round-trip during ingest — bounds peak memory regardless of file size |
| `embedding.batch_concurrency` | 4 | Number of batches allowed to run concurrently during ingest — peak memory stays roughly `upload_batch_size * batch_concurrency`, not file-sized |

Override the YAML file location via the `SETTINGS_YAML` mechanism in `YamlConfigLoader`, or edit `config/config.yaml` directly (it's mounted/copied into the image and version-controlled).

## Fixed constants (not environment-overridable)

Defined in `app/core/config/storage.py`, used by `dependencies/file_validation.py`:

- `ALLOWED_MIME_TYPES` / `ALLOWED_EXTENSIONS`: `.pdf`, `.docx`, `.txt`, `.csv`, `.json`, `.jpg`/`.jpeg`, `.png`, `.gif` — upload-time acceptance list. Note only `.pdf`, `.txt`, `.md` are actually **parseable** for ingest (see `ParserFactory` in [Detailed Components](en/DETAILED_COMPONENTS.md)); `.md` is parseable but not in the upload allow-list.
- `MIME_TYPE_MAPPING`: cross-checked against the declared extension to reject MIME/extension mismatches with a 415.
