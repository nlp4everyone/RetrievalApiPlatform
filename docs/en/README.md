# 🚀 RetrievalApiPlatform — Retrieval Engine for RAG Systems

An OpenAI-API-compatible **Retrieval Engine** for Retrieval-Augmented Generation (RAG) systems, built with FastAPI. It exposes `Files` and `Vector Stores` endpoints that are drop-in compatible with the official OpenAI SDKs, backed by Qdrant (vectors), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (background ingest), and Langfuse/OpenTelemetry (tracing).

<br />

## Key Features

- **OpenAI-compatible API surface** — `/v1/files` and `/v1/vector_stores` mirror the OpenAI Files and Vector Stores APIs closely enough that the stock `openai` Python SDK works against this server unmodified (see `examples/file_upload_example.py`)
- **Async ingest pipeline** — file upload returns immediately; parsing, chunking, embedding, and upserting into Qdrant happen out-of-band on a TaskIQ worker (Redis Streams broker)
- **Pluggable parsers** — `.txt`, `.md`, `.pdf` (via LlamaParse) today, registered through a `ParserFactory` keyed by file extension
- **Configurable chunking** — [Chonkie](https://docs.chonkie.ai)-based chunker with `recursive` / `sentence` / `token` / `character` strategies
- **Vector search** — dense embeddings via an OpenAI-compatible vLLM endpoint, similarity search against Qdrant with per-vector-store collections
- **Full tracing** — ingest and search pipelines are instrumented end-to-end with Langfuse (via OTel OTLP export), including embedding/retrieve/upsert spans

<br />

## Prerequisites

1. **Software**
   - Docker and Docker Compose
   - An OpenAI-compatible embedding endpoint (e.g. vLLM serving `Qwen/Qwen3-Embedding-0.6B`) reachable at `VLLM_DENSE_EMBEDDING_URL`
   - A self-hosted (or cloud) [Langfuse](https://langfuse.com) instance for tracing

2. **Hardware**
   - Ubuntu/Linux host with at least 8 CPU cores and 8GB of RAM to run the services in this repo
   - GPU recommended for the embedding server (not required by this repo itself, which only calls out to it over HTTP)

<br />

## Quick Start

```bash
git clone -b retrieval/naive-rag https://github.com/nlp4everyone/RetrievalApiPlatform.git
cd RetrievalApiPlatform
cp .env.sample .env
# edit .env: API keys, Postgres/MinIO/Qdrant/Langfuse credentials, VLLM_DENSE_EMBEDDING_URL
make up      # builds and starts postgres, redis, qdrant, minio, worker, web
make logs    # tail the web service
```

<br />

## Quick Start (Python Client)

`examples/file_upload_example.py` uses the stock `openai` SDK pointed at this server to upload a file and create a vector store from it:

```bash
python examples/file_upload_example.py
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8005/v1", api_key="token")  # FASTAPI_API_KEY

file = client.files.create(
    file=open("resources/sample.pdf", "rb"),
    purpose="fine-tune",
    expires_after={"anchor": "created_at", "seconds": 2592000},
)
vector_store = client.vector_stores.create(name="Support FAQ", file_ids=[file.id])
```

The upload returns immediately; ingest (parse → chunk → embed → upsert) runs asynchronously. Poll `GET /v1/vector_stores/{id}` and watch `status` go `in_progress` → `completed` (or `failed`).

<br />

## Integrations

- **API layer**: FastAPI
- **Vector database**: Qdrant
- **Metadata store**: PostgreSQL (`asyncpg`)
- **Object storage**: MinIO (uploaded file bytes)
- **Task queue**: Redis Streams + TaskIQ (async ingest worker)
- **Chunking**: [Chonkie](https://docs.chonkie.ai)
- **PDF parsing**: LlamaParse
- **Embeddings**: OpenAI-compatible client (`AsyncOpenAI`) against a self-hosted vLLM endpoint
- **Tracing**: Langfuse via OpenTelemetry OTLP
- **Runtime**: Docker Compose

<br />

## Documentation

- [Technical Overview](TECHNICAL_OVERVIEW.md) — architecture diagram, repository layout, Docker Compose topology
- [Detailed Components](DETAILED_COMPONENTS.md) — router/service/db/core layer deep-dive
- [Flow](FLOW.md) — sequence diagrams for upload, ingest, and search
- [Design Decisions](DESIGN_DECISIONS.md) — why things are shaped this way, and known gaps
- [Configuration Reference](../CONFIGURATION.md) — every setting, its source, and its default

<br />

## Known Gaps

See [Design Decisions](DESIGN_DECISIONS.md) for detail.

- Vector stores ingest **exactly one file** today; additional `file_ids` are accepted by the API but silently skipped by the worker
- `filters` and `ranking_options` on `POST /v1/vector_stores/{id}/search` are accepted by the schema but not yet applied
- Auth is a single shared `FASTAPI_API_KEY` — not per-user multi-tenancy, even though rows are scoped by `api_key`
- No OpenAI "vector store files" sub-resource endpoints (attach/list/detach a file on an existing vector store)

<br />

## To-Do / Roadmap

- [x] Base components, Chonkie chunking, naive search
- [x] OpenAI-compatible Files + Vector Stores endpoints
- [x] Async ingest via TaskIQ + Redis
- [x] LlamaParse PDF parsing (UndatasIO parser removed)
- [x] Langfuse/OpenTelemetry tracing across ingest + search
- [x] Request ID correlation across HTTP + worker
- [ ] Multi-file ingest per vector store
- [ ] Apply metadata filters and ranking options in search
- [ ] Vector store file sub-resource endpoints (attach/list/detach)
- [ ] `.docx` parser (extension is upload-accepted but has no registered parser)