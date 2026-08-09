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
   - A dense embedding endpoint at `DENSE_EMBEDDING_URL` — either OpenAI-compatible (e.g. vLLM serving `Qwen/Qwen3-Embedding-0.6B`) or a Text Embeddings Inference server. [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) is the companion repo that serves exactly this, on the ports this repo already defaults to — see [Embedding Server](#embedding-server)
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
```

**1. Start Qdrant first.** It is an external service, not part of `make up`, and the app has nothing to connect to without it — see [Vector Store (Qdrant)](#vector-store-qdrant) for the details:

```bash
docker run -d --name qdrant_db --restart always \
  -p 6333:6333 -p 6334:6334 \
  -e QDRANT__SERVICE__API_KEY=change-me \
  -v qdrant_data:/qdrant/storage \
  qdrant/qdrant:v1.19

curl -s http://localhost:6333/healthz    # → healthz check passed
```

**2. Then configure and start the app stack:**

```bash
cp .env.sample .env
# edit .env: API keys, Postgres/MinIO/Qdrant/Langfuse credentials, embedding endpoint,
#            parsing keys (LLAMAPARSE_API_KEY, UNSTRUCTURED_API_KEY/UNSTRUCTURED_API_URL),
#            and the provider switches (EMBEDDING_PROVIDER, CHUNKING_PROVIDER,
#            PDF_PARSER_PROVIDER, VECTOR_STORE_PROVIDER)
# QDRANT_URL=http://172.17.0.1:6333 and QDRANT_API_KEY must match the key set in step 1
make up      # builds and starts postgres, redis, minio, worker, web
make logs    # tail the web service
```

The embedding endpoint has to be reachable *before* `make up` too — startup probes it and fails the boot if it isn't. See [Embedding Server](#embedding-server) below.

<br />

## Vector Store (Qdrant)

Qdrant is **not** part of this stack. It is an external service the app addresses by URL — `QDRANT_URL` + `QDRANT_API_KEY`, nothing else — so it has its own lifecycle: `make up` and `make down` never touch it, and a redeploy of the app never drops the index. Run it on this host, on another host, or use Qdrant Cloud.

Single container, no Compose file needed:

```bash
docker run -d --name qdrant_db --restart always \
  -p 6333:6333 -p 6334:6334 \
  -e QDRANT__SERVICE__API_KEY=change-me \
  -v qdrant_data:/qdrant/storage \
  qdrant/qdrant:v1.19
```

| Flag | Why |
|---|---|
| `-p 6333:6333` | HTTP API — the port in `QDRANT_URL` |
| `-p 6334:6334` | gRPC API — optional, only if you talk to Qdrant directly |
| `-e QDRANT__SERVICE__API_KEY` | Enables auth. Must match the app's `QDRANT_API_KEY`; without it Qdrant is wide open |
| `-v qdrant_data:/qdrant/storage` | Named volume — collections survive `docker rm` and image upgrades |

Managing it:

```bash
curl -s http://localhost:6333/healthz    # → healthz check passed (no API key needed)
docker logs -f qdrant_db
docker stop qdrant_db && docker start qdrant_db
docker rm -f qdrant_db                   # keeps the qdrant_data volume, and the collections in it
```

Then point the app's `.env` at it:

| Where Qdrant runs | `QDRANT_URL` in the app's `.env` |
|---|---|
| Same host, app in Compose | `http://172.17.0.1:6333` (Docker bridge gateway — **not** `localhost`, which is the app container itself) |
| Another host | `http://<host>:6333` |
| Qdrant Cloud | the cluster URL, `https://` |

Notes:

- **Version floor: `v1.17` or newer.** Plain RRF fusion works on anything from `v1.10`, but **weighted RRF** — per-branch weights on the fusion query, so the dense branch can count for more than the sparse one — landed in Qdrant `v1.17.0`. `v1.19` is the current stable release and what the pinned `qdrant-client` is matched to; running an older server means falling back to unweighted RRF
- **`QDRANT_API_KEY` must be identical on both sides.** The app's value is checked at startup, so a mismatch fails the boot rather than the first search
- **Publishing 6333 binds all interfaces.** The API key is the only thing in front of it — firewall the port, or bind to a private address (`-p 10.0.0.5:6333:6333`), before running this anywhere public
- **Upgrading** is `docker rm -f qdrant_db` then the same `docker run` with a newer tag; the named volume carries the data across
- **Swapping to Milvus** means running that instead and setting `VECTOR_STORE_PROVIDER=milvus` in the app's `.env` — the app's Compose files do not change

<br />

## Embedding Server

This repo computes no vectors itself — every embedding is an HTTP call to a model server you run separately, which is why `DENSE_EMBEDDING_URL` defaults to `http://172.17.0.1:8100/v1` (the Docker host, not a Compose service). [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) is the companion repo for that side: vLLM behind an OpenAI-compatible `/v1/embeddings` API, with the same two models this repo defaults to already wired up — `Qwen/Qwen3-Embedding-0.6B` (dense, 1024-dim) and `BAAI/bge-m3` (sparse). Its default ports are the ones expected here, so the two line up with no extra configuration.

```bash
git clone -b engine/vllm https://github.com/nlp4everyone/EmbeddingService.git
cd EmbeddingService
cp .env.sample .env   # SERVING_API_KEY must match this repo's DENSE_EMBEDDING_API_KEY
make up dense         # dense only            → :8100
# make up hybrid      # dense + sparse        → :8100 + :8101, required for SPARSE_EMBEDDING_ENABLED=true
make status           # health check → OK
make test             # sample /v1/embeddings request
```

Then point this repo at it:

| Here (`.env`) | There (`.env`) | Default |
|---|---|---|
| `DENSE_EMBEDDING_URL` | `VLLM_DENSE_EMBEDDING_PORT` | `http://172.17.0.1:8100/v1` ← `8100` |
| `SPARSE_EMBEDDING_URL` | `VLLM_SPARSE_EMBEDDING_PORT` | `http://172.17.0.1:8101` ← `8101` |
| `DENSE_MODEL_NAME` | `DENSE_MODEL_NAME` | `Qwen/Qwen3-Embedding-0.6B` |
| `SPARSE_MODEL_NAME` | `SPARSE_MODEL_NAME` | `BAAI/bge-m3` |
| `DENSE_EMBEDDING_API_KEY` / `SPARSE_EMBEDDING_API_KEY` | `SERVING_API_KEY` | must match |

Notes:

- `EMBEDDING_PROVIDER=openai` is the provider that speaks to it; `tei` is for a Text Embeddings Inference server instead. Sparse uses `SPARSE_EMBEDDING_PROVIDER=vllm`, which reads token ids from vLLM's `/tokenize` and weights from `/pooling` — endpoints vLLM exposes natively, so `make up sparse`/`hybrid` needs nothing extra
- It needs an Nvidia GPU (Compute Capability 7.0+, ≥8GB VRAM), Nvidia drivers 535.54.03+, and the Nvidia Container Toolkit. Run it on the GPU host and this repo anywhere that can reach it over HTTP
- Serving both models on one GPU splits VRAM via `DENSE_GPU_MEM_UTIL`/`SPARSE_GPU_MEM_UTIL` (default `0.6`/`0.3`) — tune those to your card
- Nothing here is coupled to that repo: any OpenAI-compatible or TEI endpoint works. It is simply the known-good pairing

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

## Known Gaps

See [Design Decisions](DESIGN_DECISIONS.md) for detail.

- Vector stores ingest **exactly one file**; more than one `file_id` is now rejected at request time with a 400 rather than silently skipped
- Of `ranking_options` on `POST /v1/vector_stores/{id}/search`, only `score_threshold` is applied; `ranker` and `rewrite_query` are accepted and ignored (`filters` **are** applied)
- Hybrid (dense + sparse) search runs only where both halves exist: `SPARSE_EMBEDDING_ENABLED` **and** a collection ingested with sparse vectors. Stores created before sparse was switched on stay dense-only — Qdrant cannot add a vector field to a live collection, so they need re-ingesting to go hybrid. `search_type: "auto"` (the default) resolves this per store; asking for `"hybrid"` on a store that cannot answer it is a 400 rather than a silent fallback
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
- [x] Hybrid search (BGE-M3 sparse vectors, fused with dense by Qdrant RRF)
- [x] Per-request `search_type` (`auto`/`dense`/`hybrid`), with pinned hybrid refused as a 400 on stores that cannot answer it
- [ ] Apply the rest of `ranking_options` in search (`score_threshold` done; `ranker`, `rewrite_query` still ignored)
- [ ] Milvus backend implementation
- [ ] Vector store file sub-resource endpoints (attach/list/detach)
- [ ] Reconcile the two allow-lists: parsers for `.csv`/`.json`/`.gif` (or drop them from upload), and add `.md`/`.doc` to `ALLOWED_EXTENSIONS`
