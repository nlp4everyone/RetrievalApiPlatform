# 🚀 RetrievalApiPlatform — Retrieval Engine for RAG Systems

An OpenAI-API-compatible **Retrieval Engine** for Retrieval-Augmented Generation (RAG) systems, built with FastAPI. It exposes `Files` and `Vector Stores` endpoints that are drop-in compatible with the official OpenAI SDKs, backed by a pluggable vector database (Qdrant today), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (background ingestion), and Langfuse/OpenTelemetry (tracing).

<br />

## Key Features

- **OpenAI-compatible API surface** — `/v1/files` and `/v1/vector_stores` mirror the OpenAI Files and Vector Stores APIs closely enough that the stock `openai` Python SDK works against this server unmodified (see `examples/file_upload_example.py`)
- **Staged pipelines** — ingestion (`download → parse → chunk → embed_index`) and retrieval (`embed_query → retrieve → fuse`) are built from `BaseStage` classes run by a shared `Pipeline`. Adding a step is adding a class, not editing an orchestration function
- **Streaming ingestion with bounded memory** — the last step embeds and upserts **one batch at a time** under a single semaphore, so writes start with the first batch and peak memory is `batch_size × concurrency` rather than file-sized. The worker keeps its I/O thread pool separate from its CPU pool and caps concurrent downloads
- **Swappable providers everywhere** — parsing, chunking, embedding, and the vector database each sit behind a `base.py` interface with a `provider/` directory and a `from_settings()` facade, selected by one environment variable
- **Async ingestion** — file upload returns immediately; the pipeline runs out-of-band on a TaskIQ worker (Redis Streams broker)
- **Provider-agnostic vector search** — dense similarity search through `BaseAsyncVectorStore`; Qdrant is implemented, Milvus is wired end-to-end as a placeholder. Metadata filters are expressed in a backend-neutral tree and translated per backend
- **Tracing that follows the work** — the pipeline (not each stage) opens the spans, so the Langfuse trace shape stays correct as stages change. W3C trace context is propagated into the worker, so ingestion observations land inside the HTTP request's trace

<br />

## Prerequisites

1. **Software**
   - Docker and Docker Compose
   - Python 3.11–3.13 if running outside Docker (`requires-python = ">=3.11,<3.14"`, per `unstructured`'s constraint)
   - A dense embedding endpoint — either OpenAI-compatible (e.g. vLLM serving `Qwen/Qwen3-Embedding-0.6B` at `VLLM_DENSE_EMBEDDING_URL`) or a Text Embeddings Inference server at `TEI_EMBEDDING_URL`
   - API keys for the parsing services you actually use: `LLAMAPARSE_API_KEY` for PDFs, `UNSTRUCTURED_API_KEY` (+ `UNSTRUCTURED_API_URL`) for every other format. Each key is only checked when its provider is first used, so a PDF-only deployment needs no Unstructured key
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
# edit .env: API keys, Postgres/MinIO/Qdrant/Langfuse credentials, embedding endpoint,
#            parsing keys (LLAMAPARSE_API_KEY, UNSTRUCTURED_API_KEY/UNSTRUCTURED_API_URL),
#            and the provider switches (EMBEDDING_PROVIDER, CHUNKING_PROVIDER,
#            PDF_PARSER_PROVIDER, VECTOR_STORE_PROVIDER)
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

The upload returns immediately; ingestion (download → parse → chunk → embed_index) runs asynchronously. Poll `GET /v1/vector_stores/{id}` and watch `status` go `in_progress` → `completed` (or `failed`).

<br />

## Integrations

| Concern | Implementation | Selected by |
|---|---|---|
| API layer | FastAPI | — |
| Vector database | Qdrant (Milvus stubbed) | `VECTOR_STORE_PROVIDER` |
| Metadata store | PostgreSQL (`asyncpg`) | — |
| Object storage | MinIO (uploaded file bytes) | — |
| Task queue | Redis Streams + TaskIQ | — |
| Parsing | LlamaParse (`.pdf`), Unstructured API (`.txt`, `.md`, `.docx`, `.doc`, images) — both return Markdown | `PDF_PARSER_PROVIDER` (PDF only) |
| Chunking | [Chonkie](https://docs.chonkie.ai) or `langchain_text_splitters` | `CHUNKING_PROVIDER` |
| Embeddings | OpenAI-compatible endpoint or Text Embeddings Inference | `EMBEDDING_PROVIDER` |
| Tracing | Langfuse via OpenTelemetry OTLP | — |
| Runtime | Docker Compose | — |

<br />

## Documentation

- [Technical Overview](TECHNICAL_OVERVIEW.md) — architecture diagram, repository layout, Docker Compose topology
- [Detailed Components](DETAILED_COMPONENTS.md) — api/service/pipeline/component/db layer deep-dive
- [Flow](FLOW.md) — step-by-step diagrams for upload, ingestion, and search
- [Design Decisions](DESIGN_DECISIONS.md) — why things are shaped this way, and known gaps
- [Configuration Reference](../CONFIGURATION.md) — every setting, its source, and its default

<br />

## Known Gaps

See [Design Decisions](DESIGN_DECISIONS.md) for detail.

- Vector stores ingest **exactly one file**; more than one `file_id` is now rejected at request time with a 400 rather than silently skipped
- `ranking_options` on `POST /v1/vector_stores/{id}/search` is accepted by the schema but not yet applied (`filters` **are** applied)
- Only `SearchType.DENSE` is implemented — keyword/BM25 and hybrid retrieval have seams (`BaseRetriever`, `BaseFusion`) but no implementations
- Milvus is wired through config, `VectorStoreType`, and `VectorStoreFactory`, but every method raises `NotImplementedError`
- Auth is a single shared `FASTAPI_API_KEY` — not per-user multi-tenancy, even though rows are scoped by `api_key`
- No OpenAI "vector store files" sub-resource endpoints (attach/list/detach a file on an existing vector store)
- `.csv`, `.json`, and `.gif` pass upload validation but have no registered parsing provider; conversely `.md` and `.doc` can be parsed but are not upload-accepted, so they always get a 415

<br />

## To-Do / Roadmap

- [x] Base components, Chonkie chunking, naive search
- [x] OpenAI-compatible Files + Vector Stores endpoints
- [x] Async ingest via TaskIQ + Redis
- [x] LlamaParse PDF parsing (UndatasIO parser removed)
- [x] Langfuse/OpenTelemetry tracing across ingest + search
- [x] Request ID correlation across HTTP + worker
- [x] Provider abstraction for parsing, chunking, embedding, and the vector store
- [x] Staged pipeline framework for ingestion and retrieval
- [x] Apply metadata filters in search
- [x] Unstructured API parsing for `.txt`/`.md`/`.docx`/`.doc`/images (replaces the in-process decoder; every format now yields Markdown)
- [x] Streaming ingestion: embed and index merged into one stage, peak memory independent of file size
- [x] Split I/O and CPU thread pools, cap concurrent downloads in the worker
- [ ] Multi-file ingest per vector store
- [ ] Hybrid search (keyword/BM25 retriever + fusion strategy)
- [ ] Apply ranking options in search
- [ ] Milvus backend implementation
- [ ] Vector store file sub-resource endpoints (attach/list/detach)
- [ ] Reconcile the two allow-lists: parsers for `.csv`/`.json`/`.gif` (or drop them from upload), and add `.md`/`.doc` to `ALLOWED_EXTENSIONS`
