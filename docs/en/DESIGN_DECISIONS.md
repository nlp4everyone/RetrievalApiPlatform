# Design Decisions

## Why an OpenAI-compatible surface

`/v1/files` and `/v1/vector_stores` are modeled on the OpenAI Files and Vector Stores APIs — same object shapes, same error envelope, same auth style. This is a **partial** compatibility layer by design, not an oversight — see Known Gaps below.

**Advantages**
- Compatible with existing SDKs: any tool already built against the `openai` SDK — RAG frameworks, agent SDKs, internal scripts — can just swap `base_url`, no bespoke client needed.
- Lower learning cost: users already familiar with the OpenAI Files/Vector Stores API don't have to learn a new one.

**Disadvantages**
- Locked into OpenAI's API shape: object shapes and the error envelope must follow OpenAI even where that's not the best fit for this repo's own needs.
- Partial compatibility can mislead: callers may assume full parity with OpenAI (filters, ranking, multi-file...) when in practice it isn't there yet.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| A custom-designed REST API | Loses the SDK/tooling already built around the OpenAI ecosystem, raising integration effort for users |
| A custom GraphQL or RPC API | No ready-made SDK/adapter for the current RAG ecosystem, would require writing a client from scratch |

## Why a background worker via TaskIQ

Ingesting a file (parse → chunk → embed → upsert) can take anywhere from a few seconds to tens of seconds, especially for PDFs via LlamaParse or large files — too long to hold an HTTP request open synchronously. `POST /v1/vector_stores` just creates a record with `status=in_progress` and enqueues a job; the actual ingest work runs on a separate worker process (TaskIQ, Redis Streams broker), decoupled from the request lifecycle.

**Advantages**
- The request returns immediately with a predictable response time: the API process is never blocked on CPU/IO-heavy work.
- Independent scaling: `web` and `worker` are separate processes, so worker replicas can be added when ingest backs up without scaling the API.
- Better fault tolerance: jobs sit in the queue, so a worker crash or restart doesn't lose the caller's request.
- Faithful to the source API's spirit: OpenAI's own vector store creation is also async with polling, so this isn't a departure from the original API's behavior.

**Disadvantages**
- Callers don't get the result immediately: they must actively poll `GET /v1/vector_stores/{id}` instead of getting a synchronous response.
- Adds an infrastructure component that must be run and monitored separately (Redis broker, worker process), instead of everything living in a single API process.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Synchronous ingest inline in the request | Prone to HTTP timeouts on large files/complex PDFs; blocks the API process under load |
| FastAPI's built-in `BackgroundTasks` | Doesn't separate processes, can't scale independently of the API; jobs are lost if the API process restarts or crashes |

## Why Qdrant as the vector database

Each vector store in the repo maps to its own collection in Qdrant, where embeddings are stored and similarity search runs when `/v1/vector_stores/{id}/search` is called.

**Advantages**
- Built-in sparse vector / BM25 support: Qdrant lets you define `sparse_vectors_config` alongside dense vectors on the same collection — opening the door to hybrid (dense + BM25) search later without switching vector databases, just by turning on the config already present in the current wrapper.
- Natural per-collection isolation: each vector store is its own collection, matching the repo's data model directly — deleting a vector store cascades to exactly one collection, with no data bleeding between vector stores.
- Official async Python client (`AsyncQdrantClient`), matching the repo's fully async stack (FastAPI, asyncpg, TaskIQ) without needing a sync-to-async wrapper.
- Built-in quantization (binary/scalar/product) and on-disk vector storage, scaling to large volumes without implementing vector compression yourself.

**Disadvantages**
- Adds another service to run in the Docker Compose stack, on top of Postgres/MinIO/Redis.
- At the current stage (naive-rag), sparse/BM25 is only a hook in the wrapper (`sparse_vectors_config`) — it isn't wired into the real ingest pipeline yet, so the hybrid-search payoff is still potential, not realized.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| pgvector (a Postgres extension) | No native BM25/sparse vector support; would need a separate full-text search bolted on, losing the "one collection for both dense and sparse" advantage |
| Elasticsearch / OpenSearch | Strong at BM25/full-text but less optimized for pure vector similarity search than Qdrant, and heavier to operate for a service that only needs a vector store |