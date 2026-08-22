# 🚀 RetrievalApiPlatform — Retrieval Engine for RAG Systems

An OpenAI-API-compatible **Retrieval Engine** for Retrieval-Augmented Generation (RAG) systems, built with FastAPI. It exposes `Files` and `Vector Stores` endpoints that are drop-in compatible with the official OpenAI SDKs, backed by a pluggable vector database (Qdrant or Milvus, or both at once), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (background ingestion), and Langfuse/OpenTelemetry (tracing).

<br />

## Key Features

- **OpenAI-compatible API surface** — `/v1/files` and `/v1/vector_stores` mirror the OpenAI Files and Vector Stores APIs closely enough that the stock `openai` Python SDK works against this server unmodified (see `examples/file_upload_example.py`)
- **Staged pipelines** — ingestion (`download → parse → chunk → embed_index`) and retrieval (`embed_query → retrieve → fuse`) are built from `BaseStage` classes run by a shared `Pipeline`. Adding a step is adding a class, not editing an orchestration function
- **Streaming ingestion with bounded memory** — the last step embeds and upserts **one batch at a time** under a single semaphore, so writes start with the first batch and peak memory is `batch_size × concurrency` rather than file-sized. The worker keeps its I/O thread pool separate from its CPU pool and caps concurrent downloads
- **Swappable providers everywhere** — parsing, chunking, embedding, and the vector database each sit behind a `base.py` interface with a `provider/` directory and a `from_settings()` facade, selected by one environment variable
- **Async ingestion** — file upload returns immediately; the pipeline runs out-of-band on a TaskIQ worker (Redis Streams broker)
- **Provider-agnostic vector search** — dense and hybrid search through `BaseAsyncVectorStore`; Qdrant and Milvus are both implemented and can be connected at the same time, since every vector store remembers which engine holds it. Metadata filters are expressed in a backend-neutral tree and translated per backend
- **Tracing that follows the work** — the pipeline (not each stage) opens the spans, so the Langfuse trace shape stays correct as stages change. W3C trace context is propagated into the worker, so ingestion observations land inside the HTTP request's trace

<br />

## Prerequisites

1. **Software**
   - Docker and Docker Compose
   - Python 3.11–3.13 if running outside Docker (`requires-python = ">=3.11,<3.14"`, per `unstructured`'s constraint)
   - A vector database, external to this stack: [Qdrant](https://qdrant.tech/documentation/guides/installation/) `v1.17`+ (the default — `QDRANT_URL` + `QDRANT_API_KEY`) or [Milvus](https://milvus.io/docs/install_standalone-docker.md) `2.4`+ (`MILVUS_URI`, plus `MILVUS_TOKEN` when auth is on). `make up` never touches it, so a redeploy never drops the index
   - A dense embedding endpoint at `DENSE_EMBEDDING_URL` — either OpenAI-compatible (e.g. vLLM serving `Qwen/Qwen3-Embedding-0.6B`) or a Text Embeddings Inference server. [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) is the companion repo that serves exactly this, on the ports this repo already defaults to
   - API keys for the parsing services you actually use: `LLAMAPARSE_API_KEY` for PDFs, `UNSTRUCTURED_API_KEY` (+ `UNSTRUCTURED_API_URL`) for every other format. Each key is only checked when its provider is first used, so a PDF-only deployment needs no Unstructured key
   - A self-hosted (or cloud) [Langfuse](https://langfuse.com) instance for tracing

2. **Hardware**
   - Ubuntu/Linux host with at least 8 CPU cores and 8GB of RAM to run the services in this repo
   - GPU recommended for the embedding server (not required by this repo itself, which only calls out to it over HTTP)

<br />

## Quick Start

**1. Start the stack.**

```bash
git clone https://github.com/nlp4everyone/RetrievalApiPlatform.git
cd RetrievalApiPlatform
cp .env.sample .env
# edit .env: API keys, Postgres/MinIO/Qdrant/Langfuse credentials, embedding endpoint,
#            parsing keys (LLAMAPARSE_API_KEY, UNSTRUCTURED_API_KEY/UNSTRUCTURED_API_URL),
#            and the provider switches (EMBEDDING_PROVIDER, CHUNKING_PROVIDER,
#            PDF_PARSER_PROVIDER, VECTOR_STORE_PROVIDER)
make up      # builds and starts postgres, redis, minio, worker, web
make logs    # tail the web service
```

The vector store and the embedding endpoint both have to be reachable *before* `make up` — startup probes them and fails the boot if either is missing, so `QDRANT_URL`/`QDRANT_API_KEY` must match the Qdrant you are already running. See [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) for the embedding side.

**2. Upload a file and create a vector store.** `examples/file_upload_example.py` uses the stock `openai` SDK pointed at this server:

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

**3. Search it and print the hits.** Once the store reports `completed`:

```python
results = client.vector_stores.search(
    vector_store_id=vector_store.id,
    query="What is the refund window?",
    max_num_results=5,
)

for hit in results.data:
    print(f"{hit.score:.3f}  {hit.filename}")
    print(hit.content[0].text[:300], "\n")
```

Each hit carries `score`, `file_id`/`filename`, the file's `attributes`, and `content` as text chunks. `search_type` defaults to `auto` — hybrid when the store holds sparse vectors, dense otherwise; force it with `extra_body={"search_type": "hybrid"}`, which is a 400 on a store that has none.

<br />

## Integrations

| Concern | Implementation | Selected by |
|---|---|---|
| API layer | FastAPI | — |
| Vector database | Qdrant and Milvus, connectable together | `VECTOR_STORE_PROVIDER` (+ each backend's credentials) |
| Metadata store | PostgreSQL (`asyncpg`) | — |
| Object storage | MinIO (uploaded file bytes) | — |
| Task queue | Redis Streams + TaskIQ | — |
| Parsing | LlamaParse (`.pdf`), Unstructured API (`.txt`, `.md`, `.docx`, `.doc`, images) — both return Markdown | `PDF_PARSER_PROVIDER` (PDF only) |
| Chunking | [Chonkie](https://docs.chonkie.ai) or `langchain_text_splitters` | `CHUNKING_PROVIDER` |
| Embeddings | OpenAI-compatible endpoint or Text Embeddings Inference — e.g. [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) (vLLM) | `EMBEDDING_PROVIDER` |
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
- [ ] `vector_store_files` table for per-file state (the prerequisite for both items below)
- [ ] Multi-file ingest per vector store
- [x] Hybrid search (BGE-M3 sparse vectors, fused with dense by Qdrant RRF)
- [x] Per-request `search_type` (`auto`/`dense`/`hybrid`), with pinned hybrid refused as a 400 on stores that cannot answer it
- [ ] Apply the rest of `ranking_options` in search (`score_threshold` done; `ranker`, `rewrite_query` still ignored)
- [x] Milvus backend implementation, connectable alongside Qdrant
- [ ] Vector store file sub-resource endpoints (attach/list/detach)
- [ ] Reconcile the two allow-lists: parsers for `.csv`/`.json`/`.gif` (or drop them from upload), and add `.md`/`.doc` to `ALLOWED_EXTENSIONS`
- [ ] Automated tests (`pytest` is already a dev dependency; nothing uses it yet)
