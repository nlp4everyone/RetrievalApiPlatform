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

**1. Start the vector store first.** It is an external service, not part of `make up`, and the app has nothing to connect to without it. Qdrant is the default — see [Vector Store (Qdrant)](#vector-store-qdrant), or [Vector Store (Milvus)](#vector-store-milvus) for the other backend:

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
- **Swapping to Milvus** means running that instead and setting `VECTOR_STORE_PROVIDER=milvus` in the app's `.env` — the app's Compose files do not change. Both can also run side by side; see [Vector Store (Milvus)](#vector-store-milvus)

<br />

## Vector Store (Milvus)

Milvus is the second implemented backend, external in exactly the same way: `make up` and `make down` never touch it, a redeploy never drops the index, and the app knows it through `MILVUS_URI` (plus `MILVUS_TOKEN` when auth is on). Run it on this host, on another host, or use Zilliz Cloud.

Unlike Qdrant it is not one self-contained container — Milvus needs etcd for metadata and an object store for segments. Standalone mode runs both *inside* the single container (embedded etcd, local disk), and the official script writes the config files that requires, so it stays one command:

```bash
curl -sfL https://raw.githubusercontent.com/milvus-io/milvus/master/scripts/standalone_embed.sh -o standalone_embed.sh
bash standalone_embed.sh start
```

That leaves one `milvus-standalone` container:

| What it sets up | Why |
|---|---|
| Port `19530` | gRPC API — the port in `MILVUS_URI` |
| Port `9091` | Health and metrics; `/healthz` lives here, not on 19530 |
| Port `2379` | Embedded etcd — only needed if you inspect metadata directly |
| `./volumes/milvus` | Collections, etcd data and segments — the whole database is this directory, so back it up as one |
| `./user.yaml` | Config overrides written next to the script, including whether authentication is on |

Managing it:

```bash
curl -s http://localhost:9091/healthz     # → OK
docker logs -f milvus-standalone
bash standalone_embed.sh stop             # stop, keeping ./volumes/milvus
bash standalone_embed.sh start            # back up on the same data
bash standalone_embed.sh upgrade          # newer image, same data
bash standalone_embed.sh delete           # removes the container AND ./volumes/milvus
```

Then point the app's `.env` at it:

| Where Milvus runs | `MILVUS_URI` in the app's `.env` |
|---|---|
| Same host, app in Compose | `http://172.17.0.1:19530` (Docker bridge gateway) |
| Another host | `http://<host>:19530` |
| Zilliz Cloud | the cluster URI, with `MILVUS_TOKEN` set |

### Running both at once

`VECTOR_STORE_PROVIDER` decides where **new** vector stores are created. Which backends startup connects is not a separate setting: filling in a backend's credentials is what connects it. Because every vector store row records the backend holding it, having both filled in means stores on either engine stay searchable from one process:

```bash
VECTOR_STORE_PROVIDER=qdrant           # new stores land here
QDRANT_URL=http://172.17.0.1:6333      # filled in -> connected
QDRANT_API_KEY=change-me
MILVUS_URI=http://172.17.0.1:19530     # filled in -> connected too, and searchable
```

That is what makes a migration incremental rather than a cutover: flip `VECTOR_STORE_PROVIDER` to `milvus` and new stores go to Milvus while every existing Qdrant store keeps answering. Comment a backend's credentials out once nothing points at it any more.

`VECTOR_STORE_PROVIDER` must be reachable or the boot fails — new stores are created on it. The other backend is skipped with a warning if it cannot be reached, so a leftover credential block is not fatal; a store held by a backend that is not connected fails at query time with a `RuntimeError` naming it.

Notes:

- **Milvus `2.4`+** for `hybrid_search` with `RRFRanker`; verified against `3.0` with the pinned `pymilvus`
- **The script sets no restart policy**, unlike the Qdrant container above — Milvus will not come back after a host reboot until you run `docker update --restart always milvus-standalone`
- **Authentication is off by default**, which is why `MILVUS_TOKEN` can be left empty. Turn it on in `user.yaml` (`common.security.authorizationEnabled: true`), restart, and set `MILVUS_TOKEN=<user>:<password>` — the stock root credential is `root:Milvus`. Publishing 19530 with auth off means anyone reaching the port can read or drop every collection, so firewall it or bind it to a private address
- **Every listed backend must be reachable at startup** or the boot fails — a half-connected service would answer some vector stores and 500 on the rest
- **Collection names are the id verbatim.** Milvus allows only letters, digits and underscores, which is why vector store ids use one: `vs_a1b2…` is both the id and the collection name, on Qdrant and on Milvus alike, so a store reads the same in Attu as in Qdrant's dashboard. Stores created before this scheme carry a hyphen (`vs-a1b2…`) and are still folded to `vs_a1b2…` on Milvus only
- **Collections are loaded on demand.** Milvus serves only loaded collections and a restarted server comes back with everything unloaded, so a search that hits that loads the collection and retries once
- **Reads are `Strong` consistency**, matching Qdrant's read-your-writes: a store polled to `completed` is searchable immediately. Relax it in `AsyncMilvusVectorStore.__init__` to trade freshness for latency

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

## Known Gaps

See [Design Decisions](DESIGN_DECISIONS.md) for detail.

- Vector stores ingest **exactly one file**; more than one `file_id` is now rejected at request time with a 400 rather than silently skipped. There is no `vector_store_files` table, so a store's progress is a single `status` column — multi-file needs per-file state modelled first, not just a relaxed check
- `file_counts` is derived from that single status (`completed=1` or `failed=1`), never counted per file
- No automated test suite: `pytest`/`pytest-asyncio` are in the dev group, but there is no `tests/` directory
- Of `ranking_options` on `POST /v1/vector_stores/{id}/search`, only `score_threshold` is applied; `ranker` and `rewrite_query` are accepted and ignored (`filters` **are** applied)
- Hybrid (dense + sparse) search runs only where both halves exist: `SPARSE_EMBEDDING_ENABLED` **and** a collection ingested with sparse vectors. Stores created before sparse was switched on stay dense-only — neither backend can add a vector field to a live collection, so they need re-ingesting to go hybrid. `search_type: "auto"` (the default) resolves this per store; asking for `"hybrid"` on a store that cannot answer it is a 400 rather than a silent fallback
- Weighted RRF is Qdrant-only in principle, but neither backend uses it yet — both fuse with plain RRF, tuned only by `retrieval.rrf_k`
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
- [ ] `vector_store_files` table for per-file state (the prerequisite for both items below)
- [ ] Multi-file ingest per vector store
- [x] Hybrid search (BGE-M3 sparse vectors, fused with dense by Qdrant RRF)
- [x] Per-request `search_type` (`auto`/`dense`/`hybrid`), with pinned hybrid refused as a 400 on stores that cannot answer it
- [ ] Apply the rest of `ranking_options` in search (`score_threshold` done; `ranker`, `rewrite_query` still ignored)
- [x] Milvus backend implementation, connectable alongside Qdrant
- [ ] Vector store file sub-resource endpoints (attach/list/detach)
- [ ] Reconcile the two allow-lists: parsers for `.csv`/`.json`/`.gif` (or drop them from upload), and add `.md`/`.doc` to `ALLOWED_EXTENSIONS`
- [ ] Automated tests (`pytest` is already a dev dependency; nothing uses it yet)
