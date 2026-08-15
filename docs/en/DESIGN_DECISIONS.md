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
- A new backend can be wired end-to-end (config, enum, factory, startup) and merged before it works, which is how the Milvus backend landed: filling in method bodies changed nothing above `app.db`. Because the factory keys connections by provider and every vector store row names its own, more than one backend can be connected at once — a migration is incremental rather than a cutover.

**Disadvantages**
- The lowest-common-denominator problem: the interface can only expose what every backend can do, so backend-specific features (Qdrant's sparse vectors, quantization rescore) need either a widened contract or an escape hatch.
- More files per capability than a direct call would need, and one more indirection when debugging.
- Config can leave a set of backends connected that does not cover the data: connection settings are checked at startup, but nothing cross-checks them against the providers existing rows reference. Remove the credentials of a backend that still holds stores and the mistake surfaces as a `RuntimeError` on the first search of one of them, not at boot.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Call each vendor SDK directly where it's needed | Backend choice leaks into services and pipelines; swapping means touching every call site |
| A single `if provider == ...` dispatch inside each service | Works for two providers and rots at four; puts every backend's imports on the hot path whether used or not |

## Why the embedding models are served by a separate deployment

Every embedding is an HTTP call to a model server this repo does not run. `EmbeddingService` ([`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService)) is that server — vLLM serving `Qwen/Qwen3-Embedding-0.6B` (dense) and `BAAI/bge-m3` (sparse) behind an OpenAI-compatible API — and it is a separate repo and a separate Compose stack, not a service in `compose_*.yml`.

**Advantages**
- The web/worker image stays CPU-only: no CUDA layers, no model weights, no GPU requirement to run tests or the API. The GPU box and the API box can be different machines, or different scaling groups — a model server is expensive and shared, a stateless API replica is neither.
- Model lifecycle decouples from application lifecycle. Swapping the embedding model, changing GPU memory split, or restarting vLLM does not redeploy this service; `check_connection()` at startup is the only coupling, and it caches the vector dimension from whatever is actually answering.
- The contract is an API, not a library, so anything speaking OpenAI `/v1/embeddings` or TEI substitutes freely — a hosted provider, a shared cluster endpoint, someone else's server. `EmbeddingService` is the known-good default, not a dependency.
- It keeps the two halves independently useful: that repo serves any consumer, this one consumes any server.

**Disadvantages**
- Two repos to clone and two `.env` files to keep aligned before the first ingestion works — the port/model/API-key pairing is documented in [Embedding Server](README.md#embedding-server) precisely because nothing enforces it.
- Embedding cost now includes a network hop per batch, and a startup that used to fail on a missing library now fails on an unreachable host.
- A dimension mismatch (server changed model, collection did not) surfaces at ingest time, not at config time.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| Add vLLM as a service in this Compose stack | Forces a GPU on anyone running the API, ties model restarts to application deploys, and makes the common "shared model server, several consumers" setup impossible |
| Load the embedding model in-process (`sentence-transformers`) | Puts model weights and CUDA in the worker image, makes the worker's memory profile model-shaped rather than batch-shaped, and every API replica pays for its own copy |

## Why the vector store is provider-agnostic (Qdrant and Milvus)

Each vector store in the repo maps to its own collection, reached through `BaseAsyncVectorStore` rather than a Qdrant client. The provider is recorded **on the vector store row**, not read from config at query time — and since every backend with credentials filled in is connected, more than one engine can be served in the same process.

**Advantages**
- Existing collections keep working after `VECTOR_STORE_PROVIDER` changes: `get_store()` is passed the provider the store was created with, so flipping the default only affects new stores — as long as the old backend's credentials stay in place, which is how a migration runs incrementally instead of as a cutover.
- Filters are expressed once as a backend-neutral tree (`FieldCondition` / `FilterGroup`) and translated per backend, so the OpenAI-compatible request schema and the query language stay decoupled.
- `ensure_collection` is deliberately separate from `insert_documents`. Folding creation into the insert path forces every concurrent batch to race on a check-then-act — which is why the previous code had to run its first batch alone. Creating once up front makes every insert a pure write, so they all run concurrently.
- Qdrant specifically: sparse vectors live on the same collection as the dense ones and it fuses both branches server-side (`prefetch` + `FusionQuery(RRF)`), so hybrid search costs one round-trip and no in-process merge; plus natural per-collection isolation matching the data model, an official async client matching the fully async stack, and built-in quantization and on-disk storage.
- Milvus reaches the same contract by a different route — `hybrid_search` with an `RRFRanker`, one request carrying every query vector rather than Qdrant's parallel fan-out — which is the evidence that the interface really is neutral: implementing it changed nothing above `app.db`.

**Disadvantages**
- The abstraction is validated by two backends, which is enough to expose the lowest-common-denominator cost concretely: Qdrant names vector fields after the model id while Milvus rejects that and uses fixed names, Milvus still folds the legacy `vs-…` collection names to `vs_…` because it allows no hyphens, and it serves only loaded collections while Qdrant has no such state. Each of those is absorbed inside its provider, so the surface stays clean at the price of provider code that is not symmetric.
- Every connected backend is one more external service to run, monitor and back up — and since they are outside the Compose stack, nothing in `make up` will tell you one is missing until the boot probe fails.
- Pushing fusion into the backend means the `BaseFusion` seam is unused by the one case it was written for; a backend that cannot fuse server-side would have to bring its own strategy back into the process.
- Score semantics do not survive the abstraction intact: `score_threshold` is a cosine floor on Qdrant and a `radius` on Milvus, and on hybrid it can only be applied to the dense branch on either engine. The contract is the same, the numbers behind it are not exactly.

**Alternatives considered**

| Option | Why not chosen |
|---|---|
| pgvector (a Postgres extension) | No native BM25/sparse vector support; would need a separate full-text search bolted on, losing the "one collection for both dense and sparse" advantage |
| Elasticsearch / OpenSearch | Strong at BM25/full-text but less optimized for pure vector similarity search than Qdrant, and heavier to operate for a service that only needs a vector store |
| Committing to the Qdrant client directly | Cheaper today, but the backend choice would leak into services and pipelines, making a later migration a rewrite rather than a new provider |
| One backend at a time — resolve `VECTOR_STORE_PROVIDER` at query time | Makes changing backend a cutover: every existing store becomes unreadable the moment the variable flips, so the only migration path is re-ingesting everything before the switch. Recording the provider per row and connecting several at once costs one column and a dict of connections, and turns that cutover into a gradual drain |

## Why `SearchType` names the whole retrieval shape

Retrieval is selected by a single `SearchType` value that the factory resolves into a `_RetrievalPlan(retrievers, fusion)` — rather than letting a caller pass a retriever list and a fusion strategy separately.

The two always have to agree: several retrievers with `PassthroughFusion` silently throws half the results away. Naming the combination keeps the invalid states unrepresentable, and `PassthroughFusion` raises rather than truncating if it's ever handed more than one candidate list. Search type is a per-call argument rather than configuration, because two queries against the same vector store can reasonably want different retrieval.

Hybrid then arrived as one more enum value plus one branch in `_build_plan()`, exactly as the shape predicted. The API surfaces it as `search_type: "auto" | "dense" | "hybrid"`, defaulting to `"auto"` — which is `resolve_search_type()` answering per search from what the collection holds, because the honest input to that decision is the collection's schema, not the caller's preference.

Pinning `"hybrid"` is checked, not attempted: `hybrid_unavailable_reason()` is consulted first and a 400 comes back naming which half is missing. Falling back to dense would be worse than refusing — a caller who asked for hybrid and silently got dense results would be measuring retrieval quality against a configuration they don't think they are running, which is far harder to notice than a rejected request. That one function answers both the "what should run" and the "why can't it" question, so the resolution and the error message cannot drift apart.

The cost is that adding a search type means touching the enum and the factory rather than just passing different arguments — a deliberate trade of caller flexibility for an invariant that can't be broken by accident.

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

- **Single-file ingestion.** More than one `file_id` is rejected with a 400 at request time, and `IngestionService` re-checks and marks the store `failed` rather than reporting `completed` on an empty store. The obstacle is the schema, not the check: there is no `vector_store_files` table, so a store's progress is one `status` column — which cannot express "3 files done, 1 failed". Multi-file means modelling per-file state first, the way OpenAI's own `vector_store.files` sub-resource does.
- **`file_counts` is derived, not counted.** `_calculate_file_counts` maps the store's single status to `completed=1` / `failed=1`; no other value is reachable. The field is present for OpenAI compatibility and follows from the gap above.
- **No automated test suite.** `pytest` and `pytest-asyncio` are declared in the dev dependency group, but there is no `tests/` directory and not a single test file — `examples/file_upload_example.py` against a running stack is the only end-to-end check. The layering is built for testability (stages take a plain context, `IngestionService` imports no TaskIQ, providers sit behind interfaces) but nothing exercises it.
- **The `"fuse"` fallback.** Sending `{"type": "auto"}` explicitly (instead of omitting `chunking_strategy`) falls through to a `"fuse"` strategy that `IngestionService` skips — the store reports `completed` while nothing was indexed. Omitting the field takes the correct `"auto"` path.
- **`ranking_options` is only partly applied.** `score_threshold` now reaches the backend (on the dense branch — applied to RRF output it would drop everything), but `ranker` and `rewrite_query` are still accepted and ignored, and quantization rescore is not surfaced on the vector store contract. `filters` **are** applied.
- **List queries are truncated.** If `query` is a list, only the first element is used.
- **Single-tenant auth.** One shared `FASTAPI_API_KEY`, even though rows are scoped by `api_key` as if for multi-tenancy.
- **Orphaned objects.** If the Postgres insert fails after a successful MinIO upload, the object is left behind — logged, with no compensating cleanup.
- **Hybrid retrieval depends on when a store was ingested.** `resolve_search_type` only returns `HYBRID` for collections that already carry sparse vectors, and neither backend can add a vector field to a live collection — so enabling `SPARSE_EMBEDDING_ENABLED` leaves every existing store dense-only until it is re-ingested. A caller can now find this out by asking: `search_type: "hybrid"` on such a store returns a 400 naming the reason. There is still no way to *read* a store's mode — no field on `VectorStoreObject` says whether it holds sparse vectors, so discovering it means attempting a search.
- **The two allow-lists disagree.** `.csv`, `.json`, and `.gif` pass upload validation but have no registered parsing provider. Conversely `.md` and `.doc` can be parsed but are not in `ALLOWED_EXTENSIONS`, and `validate_file_type` gates on extension with no escape hatch — those two always get a 415 at upload.
- **Partial writes when ingestion fails.** `EmbedAndIndexStage` upserts batch by batch, so a batch failing midway leaves earlier batches in the collection even though the store is marked `failed`; there is no cleanup.
