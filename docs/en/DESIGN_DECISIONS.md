# Design Decisions

## Why an OpenAI-compatible surface

`/v1/files` and `/v1/vector_stores` are modeled on the OpenAI Files and Vector Stores APIs — same object shapes, same error envelope, same auth style. This is a **partial** compatibility layer by design, not an oversight — see Known Gaps below.

**Advantages**
- Compatible with existing SDKs: any tool already built against the `openai` SDK — RAG frameworks, agent SDKs, internal scripts — can just swap `base_url`, no bespoke client needed.
- Lower learning cost: users already familiar with the OpenAI Files/Vector Stores API don't have to learn a new one.

**Disadvantages**
- Locked into OpenAI's API shape: object shapes and the error envelope must follow OpenAI even where that's not the best fit for this repo's own needs.
- Partial compatibility can mislead: callers may assume full parity with OpenAI (ranking options, multi-file...) when in practice it isn't there yet.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| A custom-designed REST API | Loses the SDK/tooling already built around the OpenAI ecosystem, raising integration effort for users |
| A custom GraphQL or RPC API | No ready-made SDK/adapter for the current RAG ecosystem, would require writing a client from scratch |

## Why a background worker via TaskIQ

Ingesting a file (download → parse → chunk → embed_index) can take anywhere from a few seconds to tens of seconds, especially when parsing goes through an external service (LlamaParse, the Unstructured API) or the file is large — too long to hold an HTTP request open synchronously. `POST /v1/vector_stores` just creates a record with `status=in_progress` and enqueues a job; the actual work runs on a separate worker process (TaskIQ, Redis Streams broker), decoupled from the request lifecycle.

**Advantages**
- The request returns immediately with a predictable response time: the API process is never blocked on CPU/IO-heavy work.
- Independent scaling: `web` and `worker` are separate processes, so worker replicas can be added when ingestion backs up without scaling the API.
- Better fault tolerance: jobs sit in the queue, so a worker crash or restart doesn't lose the caller's request.
- Faithful to the source API's spirit: OpenAI's own vector store creation is also async with polling, so this isn't a departure from the original API's behavior.

**Disadvantages**
- Callers don't get the result immediately: they must actively poll `GET /v1/vector_stores/{id}` instead of getting a synchronous response.
- Adds an infrastructure component that must be run and monitored separately (Redis broker, worker process), instead of everything living in a single API process.
- Observability gets harder: without extra work, the ingestion job's spans would form a trace disconnected from the request that caused it. That is why the W3C trace context is injected into the task payload and re-extracted worker-side.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Synchronous ingestion inline in the request | Prone to HTTP timeouts on large files/complex PDFs; blocks the API process under load |
| FastAPI's built-in `BackgroundTasks` | Doesn't separate processes, can't scale independently of the API; jobs are lost if the API process restarts or crashes |

## Why staged pipelines instead of ingest/search functions

Ingestion and retrieval are each expressed as a list of `BaseStage` classes run by a shared `Pipeline`, with one mutable context object threaded through them, rather than as a procedural function that does the work top to bottom.

**Advantages**
- Adding a step is adding a class plus one line in a factory — sparse/BM25 embedding, an OCR pass, a dedup filter, a reranker all land the same way, with no edit to the code that runs them.
- Tracing stops being a per-function concern. `Pipeline.run()` is the only place that opens spans, so the trace shape is a property of the pipeline and stays correct when stages are added, removed, or reordered — no stage can forget to instrument itself, and no stage imports OpenTelemetry.
- Stages are independently testable: each one reads and writes a plain dataclass, with no broker, no HTTP, and no tracer required.
- The two pipelines share one runner, so an improvement to error handling or span nesting benefits both.

**Disadvantages**
- More indirection to read through: following one ingestion end-to-end means opening a factory, a context, and four stage files instead of a single function.
- The shared mutable context is a weaker contract than explicit arguments — a stage can technically read a field an earlier stage never populated, and only fail at runtime.
- Overhead is unwarranted for a pipeline that will only ever have two steps; this pays off because both pipelines are expected to grow.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Procedural `load_and_chunk_file()` / `embed_and_upload_chunks()` (the previous shape) | Every new step means editing an existing function, and every step has to remember to open its own span; the trace shape drifted from the code shape |
| A third-party orchestration framework (Prefect, Dagster, LangChain chains) | Far more machinery than a four-step in-process pipeline needs, and it would own the tracing integration this repo wants to keep pointed straight at Langfuse |

## Why every capability sits behind a provider interface

Parsing, chunking, embedding, and the vector database each follow the same shape: a `base.py` interface, a `provider/` directory of implementations, and a facade whose `from_settings()` builds the one named by an environment variable. Provider names are validated in `settings.py`, so a typo fails at startup rather than at first use.

**Advantages**
- Swapping a backend is an `.env` change, not a code change — useful when comparing chunkers or moving between an OpenAI-compatible embedding server and TEI.
- The rest of the codebase only ever sees the interface, so a backend swap can't leak upward. `app/db/vector_store/types.py` is explicitly forbidden from importing a vendor SDK for exactly this reason.
- Backends that aren't installed cost nothing: `VectorStoreFactory` addresses backends by module path and imports them on first use, so a missing `pymilvus` only matters if Milvus is actually requested — and it keeps the import graph acyclic.
- A new backend can be wired end-to-end (config, enum, factory, startup) and merged before it works, which is what the Milvus placeholder is: enabling it later is filling in method bodies, with no change above `app.db`.

**Disadvantages**
- The lowest-common-denominator problem: the interface can only expose what every backend can do, so backend-specific features (Qdrant's sparse vectors, quantization rescore) need either a widened contract or an escape hatch.
- More files per capability than a direct call would need, and one more indirection when debugging.
- A placeholder backend that raises `NotImplementedError` is discoverable in config but not usable — a misconfiguration surfaces as a runtime error rather than a startup one.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Call each vendor SDK directly where it's needed | Backend choice leaks into services and pipelines; swapping means touching every call site |
| A single `if provider == ...` dispatch inside each service | Works for two providers and rots at four; puts every backend's imports on the hot path whether used or not |

## Why the vector store is provider-agnostic (with Qdrant as the implementation)

Each vector store in the repo maps to its own collection, reached through `BaseAsyncVectorStore` rather than a Qdrant client. The provider is recorded **on the vector store row**, not read from config at query time.

**Advantages**
- Existing collections keep working after `VECTOR_STORE_PROVIDER` changes: `get_store()` is passed the provider the store was created with, so flipping the default only affects new stores.
- Filters are expressed once as a backend-neutral tree (`FieldCondition` / `FilterGroup`) and translated per backend, so the OpenAI-compatible request schema and the query language stay decoupled.
- `ensure_collection` is deliberately separate from `insert_documents`. Folding creation into the insert path forces every concurrent batch to race on a check-then-act — which is why the previous code had to run its first batch alone. Creating once up front makes every insert a pure write, so they all run concurrently.
- Qdrant specifically: built-in sparse vector / BM25 support on the same collection (the hook for later hybrid search), natural per-collection isolation matching the data model, an official async client matching the fully async stack, and built-in quantization and on-disk storage.

**Disadvantages**
- Adds another service to run in the Docker Compose stack, on top of Postgres/MinIO/Redis.
- The abstraction is currently validated by exactly one working backend, so the interface may not be as backend-neutral as intended until a second one is actually implemented.
- Sparse/BM25 remains only a hook in the Qdrant wrapper — the hybrid-search payoff is still potential, not realized.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| pgvector (a Postgres extension) | No native BM25/sparse vector support; would need a separate full-text search bolted on, losing the "one collection for both dense and sparse" advantage |
| Elasticsearch / OpenSearch | Strong at BM25/full-text but less optimized for pure vector similarity search than Qdrant, and heavier to operate for a service that only needs a vector store |
| Committing to the Qdrant client directly | Cheaper today, but the backend choice would leak into services and pipelines, making a later migration a rewrite rather than a new provider |

## Why `SearchType` names the whole retrieval shape

Retrieval is selected by a single `SearchType` value that the factory resolves into a `_RetrievalPlan(retrievers, fusion)` — rather than letting a caller pass a retriever list and a fusion strategy separately.

The two always have to agree: several retrievers with `PassthroughFusion` silently throws half the results away. Naming the combination keeps the invalid states unrepresentable, and `PassthroughFusion` raises rather than truncating if it's ever handed more than one candidate list. Search type is a per-call argument rather than configuration, because two queries against the same vector store can reasonably want different retrieval.

The cost is that adding hybrid search means touching the enum and the factory rather than just passing different arguments — a deliberate trade of caller flexibility for an invariant that can't be broken by accident.

## Why embed and index are one streaming stage, not two sequential ones

`EmbedStage` used to embed every chunk in the file before `IndexStage` wrote any of them to the vector store. Now there is only `EmbedAndIndexStage`: each batch is embedded then upserted while holding the same semaphore slot, and `Document` objects are built only after that slot is acquired.

**Advantages**
- Peak memory is genuinely bounded: `batch_size × concurrency` batches' worth of chunks/vectors/Documents, independent of file size. The old shape held *every* vector for the file in `context.embeddings` before a single byte was written.
- Writes start earlier: the first batch is in the vector store while later ones are still embedding, so total ingest time is closer to `max(embed, index)` than `embed + index`.
- Still exactly one `ensure_collection()` call up front, so there is no check-then-act race on the insert path.

**Disadvantages**
- Embed and index are no longer separately observable as two Langfuse observations; two numbers on one span (`embed_wall_clock_s` / `index_wall_clock_s`) replace that, and they need `_union_duration()` because the batches overlap.
- The merged stage does two things, so it no longer matches any single `ObservationType`: it reports as `SPAN`, not `EMBEDDING`.
- Partial writes on failure: if one batch fails midway, earlier batches **are** already in the collection while the store is marked `failed`. The old shape had this too, but here it happens sooner.
- It trades in a precondition: `embedding_dim` must be known *before* embedding, which is why `EmbeddingService.check_connection()` now caches the dimension at startup and `get_dense_embedding_dim()` raises if called before init.

**Options considered**

| Option | Why not chosen |
|---|---|
| Keep two stages, just lower `batch_size` | Does not address the root cause: `context.embeddings` still holds the whole file's vectors, so the memory ceiling still scales with file size |
| Infer `embedding_dim` from the first embed batch, then create the collection | Forces the first write to wait on the first embed, and puts collection creation back on the insert path — exactly the race that was removed |

## Why the I/O thread pool is separate from the CPU thread pool

The worker runs two `ThreadPoolExecutor`s — `IO_THREAD_POOL_SIZE=32` for MinIO transfers, `CPU_THREAD_POOL_SIZE=4` for chunking — plus an `asyncio.Semaphore(DOWNLOAD_CONCURRENCY=4)` capping concurrent file downloads.

**Advantages**
- The two kinds of work have opposite optimal shapes: I/O wants many threads parked on the network, CPU-bound work gains nothing from oversubscription but context switching. A single pool has to be sized wrong for one of them.
- Neither starves the other: a burst of slow transfers cannot occupy every slot and make chunking queue behind it, or vice versa.
- The download cap is a second layer of protection at the job level: many concurrent ingestion jobs in one worker process cannot exhaust the I/O pool by themselves.

**Disadvantages**
- Three numbers to tune instead of one, and tuning them right depends on the hardware and the real file mix — which is why all three live in `config/config.yaml`.
- More resources to initialise at startup, and a getter called before init fails with `NameError` — which is why the chunking providers call `get_cpu_executor()` rather than holding their own pool.
- The web and worker processes no longer initialise the same set of services (web skips the CPU pool and the download semaphore), so "both run the same `app/startup.py`" is now true per-function rather than for the whole sequence.

## Why every non-PDF format goes through the Unstructured API

`.txt`, `.md`, `.docx`, `.doc`, and images no longer use an in-process decoder; they go through the Unstructured API. `.pdf` still belongs to `PDF_PARSER_PROVIDER`.

**Advantages**
- Uniform output: every parsing provider now returns Markdown, so downstream chunking never has to care which format the text came from — which matters for structure-aware splitters (MarkdownHeader, for instance).
- Broader format coverage for free: `.docx`/`.doc` and images (OCR) work immediately, instead of one decoder per format.
- Document structure survives: headings by `category_depth`, lists, and tables built from the `text_as_html` metadata — none of which a plain-text decoder can provide.

**Disadvantages**
- A `.txt` file now costs a network round-trip and an API key, where it used to be `bytes.decode()`. Ingest is slower and depends on one more external service.
- The Unstructured API has no native Markdown output (only `application/json` or `text/csv`), so the Markdown rendering is this repo's code and has to track the element `category` set itself.
- Bumps the Python requirement to `>=3.11,<3.14` to match `unstructured==0.24.1`.

**Options considered**

| Option | Why not chosen |
|---|---|
| Keep the in-process decoder for `.txt`/`.md` | Two parsing paths with two different output shapes (plain text vs Markdown), forcing chunking to care about the source format |
| Run `unstructured` locally (`partition` instead of `partition_via_api`) | Pulls a heavy ML dependency set and CPU/OCR work into the worker process itself — precisely what is being kept off the hot path |

## Known gaps

- **Single-file ingestion.** More than one `file_id` is rejected with a 400 at request time, and `IngestionService` re-checks and marks the store `failed` rather than reporting `completed` on an empty store.
- **The `"fuse"` fallback.** Sending `{"type": "auto"}` explicitly (instead of omitting `chunking_strategy`) falls through to a `"fuse"` strategy that `IngestionService` skips — the store reports `completed` while nothing was indexed. Omitting the field takes the correct `"auto"` path.
- **`ranking_options` is inert.** Accepted by the schema, but score threshold and quantization rescore are not yet surfaced on the vector store contract. `filters` **are** applied.
- **List queries are truncated.** If `query` is a list, only the first element is used.
- **Single-tenant auth.** One shared `FASTAPI_API_KEY`, even though rows are scoped by `api_key` as if for multi-tenancy.
- **Orphaned objects.** If the Postgres insert fails after a successful MinIO upload, the object is left behind — logged, with no compensating cleanup.
- **Dense-only retrieval.** `SearchType.DENSE` is the only implemented value; the `BaseRetriever` / `BaseFusion` seams exist but have no keyword/BM25 implementations.
- **Milvus is a placeholder.** Wired through config, `VectorStoreType`, `VectorStoreFactory`, and startup, but every method raises `NotImplementedError`.
- **The two allow-lists disagree.** `.csv`, `.json`, and `.gif` pass upload validation but have no registered parsing provider. Conversely `.md` and `.doc` can be parsed but are not in `ALLOWED_EXTENSIONS`, and `validate_file_type` gates on extension with no escape hatch — those two always get a 415 at upload.
- **Partial writes when ingestion fails.** `EmbedAndIndexStage` upserts batch by batch, so a batch failing midway leaves earlier batches in the collection even though the store is marked `failed`; there is no cleanup.
