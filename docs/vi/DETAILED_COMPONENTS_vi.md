# Detailed Components

## API layer (`app/api/`)

Mọi thứ tồn tại chỉ vì app này được phục vụ qua HTTP. Không có gì ngoài package này import nó, và `app.components`/`app.pipelines` không bao giờ với ngược lên đây — đó chính là lý do TaskIQ worker chạy được mà không cần FastAPI trong đường import.

### `router/file_router.py` — tag "File"

Mọi endpoint đều yêu cầu `Depends(verify_api_key)`.

| Method | Path | Handler | Ghi chú |
|---|---|---|---|
| POST | `/v1/files` | `upload_file` | Form field `purpose`, `file` (qua `Depends(validate_file)`), `expires_after[anchor \| seconds]` |
| GET | `/v1/files` | `list_files` | Query qua `FileQueryRequest` (kế thừa `PaginationParams`) |
| GET | `/v1/files/{file_id}` | `get_file_by_id` | — |
| DELETE | `/v1/files/{file_id}` | `delete_file` | Trả `FileDeletedResponse` |

### `router/vector_store_router.py` — tag "Vector Stores"

| Method | Path | Handler | Ghi chú |
|---|---|---|---|
| POST | `/v1/vector_stores` | `create_vector_store` | Body `VectorStoreCreateRequest`; đẩy job ingestion. Nhiều hơn một `file_id` → 400 |
| GET | `/v1/vector_stores` | `list_vector_stores` | Query `VectorStoreQueryRequest` |
| GET | `/v1/vector_stores/{id}` | `get_vector_store` | — |
| POST | `/v1/vector_stores/{id}` | `modify_vector_store` | Kiểu OpenAI: update dùng POST, không phải PATCH |
| DELETE | `/v1/vector_stores/{id}` | `delete_vector_store` | — |
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters` và `search_type` đã áp dụng, trong `ranking_options` mới chỉ `score_threshold` |

### `dependencies.py`

`validate_file_size` (từ chối file quá `MAX_FILE_SIZE` MB), `validate_file_type` (kiểm tra MIME + extension + tính nhất quán giữa chúng qua `MIME_TYPE_MAPPING`), ghép lại thành `validate_file` — dùng như `Depends(validate_file)` trên endpoint upload.

### `security.py`

`verify_api_key` yêu cầu header `Authorization: Bearer <token>`, so sánh token với `FASTAPI_API_KEY` duy nhất được cấu hình, rồi trả token về dưới dạng `api_key` — giá trị này sau đó dùng để scope các row trong Postgres. Vì chỉ có đúng một token hợp lệ, đây thực chất là auth single-tenant, dù schema được thiết kế theo hướng multi-tenant (xem [Design Decisions](DESIGN_DECISIONS_vi.md)).

### `middleware.py`

`RequestIDMiddleware` là middleware ASGI thuần (không phải `BaseHTTPMiddleware` — loại đó sẽ reset contextvar trước khi access logger của uvicorn kịp chạy). Nó tái sử dụng header `X-Request-Id` do client gửi nếu có, nếu không thì sinh `req_{uuid4().hex}`, bind vào `request_id_ctx` (một `contextvars.ContextVar` trong `app/core/request_context.py`) trong suốt vòng đời request, và echo lại trên response. Chính ID đó được truyền vào `ingest_vector_store_files.kiq(...)` và bind lại bên trong worker task, nên một request ID xuyên suốt cả log HTTP lẫn job ingestion bất đồng bộ mà request đó kích hoạt.

`app/app.py` còn định nghĩa `GET /health` (loại khỏi OpenAPI schema), đăng ký `RequestIDMiddleware`, handler `AppBaseException` toàn cục, và một `lifespan` khởi tạo theo thứ tự tracing → embed model → Postgres (pool + `wait_for_postgres` + tạo bảng) → vector store → MinIO → pool I/O, rồi giải phóng pool I/O, các connection vector store, pool Postgres, và HTTP client của dense/sparse embedding provider ở phần dưới `yield` — cùng một hàm, nên một client mở ở trên không thể bị quên ở dưới. Ngoài ra `app.py` còn gắn hai log filter cho `uvicorn.access`: `HealthCheckLogFilter` (im lặng với `/health` khi 2xx) và `QuietAccessLogFilter` (im lặng với list/retrieve/modify khi 2xx — những route đó đã được service layer log; create/delete/search vẫn luôn hiện, và mọi lỗi non-2xx đều hiện).

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Các static method `upload_file`, `list_files`, `get_file_by_id`, `delete_file` — cả bốn đều scope theo `api_key` của người gọi, kể cả `get_file_by_id` (trước đây đọc theo `id` đơn thuần, nên một key hợp lệ có thể đọc metadata của file thuộc key khác). Luồng upload: sinh `file_id`, dựng object path `{api_key}/uploads/{uuid}_{filename}`, upload byte lên MinIO qua `get_io_executor()`, rồi insert row metadata vào Postgres. Nếu insert Postgres thất bại sau khi upload MinIO đã thành công, object bị bỏ lại thành mồ côi (chỉ log warning, không dọn dẹp) và `PostgresConnectionException` được raise.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Các static method `create`, `list`, `get`, `modify`, `delete`, `search`.

- `create` từ chối trường hợp nhiều hơn một `file_id` trước cả khi chạm database (`UnsupportedMultipleFilesException`, 400 — "từ chối ngay thay vì để caller poll một store không bao giờ hoàn thành"), ghi row Postgres kèm provider mà nó được tạo trên đó, rồi đẩy `ingest_vector_store_files.kiq(...)` cùng `request_id` và `inject_trace_context()`.
- `search` đọc row để biết `vector_store_type`, chuyển `search_request.filters` thành cây trung lập qua `normalize_search_filter`, mở root trace span, và chạy `RetrievalPipeline` ngay trong tiến trình. Kết quả trả về dưới dạng `RetrievedChunk` rồi được `convert_retrieved_chunks_to_search_results` chuyển sang hình dạng API.
- Cả hai đường đều lấy store qua `VectorStoreFactory.get_store(collection_name, provider)` — truyền vào provider ghi trên row, nên collection nằm trên backend mà deployment này không còn kết nối sẽ báo lỗi rõ ràng thay vì bị query nhầm backend.
- `_calculate_file_counts(status)` **suy ra** object `file_counts` từ đúng một cột status của store — `completed=1` khi store hoàn thành, `failed=1` khi thất bại, ngoài ra không có giá trị nào khác. Không có trạng thái theo từng file để mà đếm, nên hình dạng response vẫn tương thích OpenAI trong khi con số chỉ có thể là 1/0 (xem [Quyết định thiết kế](DESIGN_DECISIONS_vi.md#giới-hạn-đã-biết)).

### `IngestionService` (`ingestion/ingestion_service.py`)

Business logic của task nền, cố ý không import TaskIQ để có thể gọi và test mà không cần broker đang chạy. `ingest_vector_store_files(...)`:

1. Chốt chặn: `chunking_strategy` phải thuộc `("auto", "static")`, nếu không thì log, `_mark_failed`, và raise — một giá trị lạ nghĩa là `VectorStoreService.create` và worker đã lệch nhau, và fail còn hơn im lặng bỏ qua phần ingest
2. `PostgresFileStore.check_existing_files` — có yêu cầu `file_ids` nhưng không id nào tồn tại? log, `_mark_failed`, và raise. Chỉ store tạo ra **không kèm** `file_ids` nào mới nhảy thẳng tới `completed` với `usage_bytes=0`
3. Lấy `get_total_bytes` + `get_metadata_for_files`, cả hai đều dựa trên `existing_file_ids` để một id đã biến mất không được tính vào bên nào
4. Chốt chặn: đúng một file, nếu không thì log, `_mark_failed`, và raise — lớp phòng thủ thứ hai sau lần từ chối ở tầng API. Con số `0` vẫn tới được nhánh này: `get_metadata_for_files` loại bỏ row có `metadata` là `NULL`, nên một file tồn tại mà không có metadata sẽ rơi vào đây
5. `_ingest_single_file` dựng pipeline qua `build_ingestion_pipeline(...)` và chạy nó trên một `IngestionContext`, truyền `trace_context` làm parent carrier, rồi trả về `context.num_inserted`
6. Chốt chặn: `num_inserted > 0`, nếu không thì log, `_mark_failed`, và raise — pipeline vẫn chạy tới hết trên một file không rút được chữ nào, và một collection rỗng thì không đáng gọi là `completed`
7. Đánh dấu `completed` kèm `usage_bytes`; mọi lỗi đều đánh dấu `failed` rồi raise lại

Mọi chốt chặn trên tồn tại vì một lý do: `completed` là tín hiệu báo với client rằng collection đã search được, nên mỗi đường dẫn để lại collection rỗng đều phải fail thay vì đi tiếp.

## Pipeline layer (`app/pipelines/`)

### Khung (`base.py`, `pipeline.py`)

`BaseStage[ContextT]` là contract: một `name` (tên span), một `observation_type`, một `async run(context)`, cộng hai hook tuỳ chọn — `emits_span(context)` (quyết định *trước* `run`, nên có thể nhìn vào kết quả của stage trước; trả `False` vẫn chạy stage, chỉ là giữ một no-op ra khỏi trace) và `span_attributes(context)` (đọc *sau* khi `run` thành công).

`Pipeline[ContextT]` chạy các stage theo thứ tự dưới một span cha duy nhất và là **nơi duy nhất mở span**. Stage không bao giờ import module tracing. Subclass override `root_attributes()` (đặt trước mọi stage) và `result_attributes()` (đặt sau khi mọi stage thành công). `run(context, parent_carrier)` nhận một `TraceCarrier` để pipeline khởi chạy từ một job đã enqueue xuất hiện bên trong trace của request đã enqueue nó.

Lợi ích: hình dạng trace là thuộc tính của pipeline, và nó vẫn đúng khi stage được thêm, bớt, hay đảo thứ tự.

### Ingestion (`pipelines/ingestion/`)

`IngestionContext` là một dataclass mutable duy nhất xuyên suốt mọi stage — input (`vector_store_id`, `file_id`, `file_metadata`, tham số chunking), kết quả điền dần (`raw_bytes`/`content_sha256` → `text` → `chunks` → `num_inserted`, cùng `parsed_from_cache` và cặp `splitter`/`splitter_detected` do `ChunkStage` quyết định), và một dict `metrics` tự do cho những con số thuộc về span nhưng không thuộc về state của pipeline. Các property tiện lợi (`filename`, `file_extension`, `storage_bucket`, `storage_path`) đọc từ `file_metadata`.

| Stage | Làm gì | Ghi chú span |
|---|---|---|
| `DownloadStage` | Lấy byte từ MinIO, dưới `get_storage_semaphore()`, transfer chạy trên `get_io_executor()`, rồi băm chúng trên `get_cpu_executor()` | bucket/path, tên file, `file.size_bytes`, `file.content_sha256` |
| `ParseStage` | Tra cache parse trước; nếu miss thì `ParsingService.parse(bytes, ext)` → text (Markdown) | **slug** của provider đã xử lý, `parser.cache` (`hit`/`miss`), `text.num_chars` |
| `PersistTextStage` | Ghi Markdown đó về `PARSED_TEXT_BUCKET`, best effort; bỏ qua khi hit cache | bucket + `storage.path`, `parsed_text.saved`, `parsed_text.size_bytes` |
| `ChunkStage` | Chốt splitter (theo request, nếu không thì suy ra từ text), rồi `ChunkingService.split_text(text)` → chunks, chạy trên `get_cpu_executor()` | `chunk.strategy`, `chunk.detected`, `chunk.size`, `chunk.overlap`, `chunks.count`, `chunks.avg_chars` |
| `EmbedAndIndexStage` | `ensure_collection(embedding_dim, with_sparse)` một lần, rồi embed + upsert **từng batch một** | collection có được tạo mới không, `embedding.dims`, `embedding.sparse_enabled`/`sparse_model`, `embed`/`index` wall-clock, `batch.*` |

`EmbedAndIndexStage` là kết quả gộp hai stage `EmbedStage` + `IndexStage` cũ thành một vòng lặp streaming:

- Chunk được chia thành batch `EMBEDDING_UPLOAD_BATCH_SIZE`, và **một** `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY)` bao cả embed lẫn upsert của batch đó — không còn "embed xong toàn file rồi mới ghi", batch đầu tiên đã bắt đầu ghi trong khi các batch sau còn đang embed.
- `Document` chỉ được dựng *sau* khi lấy được semaphore, nên bộ nhớ đỉnh là `batch_size × concurrency` batch chunk/vector/Document, không phải toàn bộ file.
- `embedding_dim` lấy từ `get_dense_embedding_dim()` — số chiều được `EmbeddingService.check_connection()` cache lúc startup — nên collection tạo được *trước* lần embed đầu tiên. Đây chính là điều kiện để streaming khả thi: `IndexStage` cũ phải suy `embedding_dim` từ `context.embeddings[0]`, tức phải chờ embed xong.
- `embed_wall_clock_s` / `index_wall_clock_s` được tính bằng `_union_duration()`, hợp các khoảng thời gian chồng nhau, vì cộng thẳng thời lượng từng batch chạy song song sẽ đếm trùng.
- Khi có `sparse_embed_fn`, mỗi batch được embed hai lần — dense và sparse trong cùng một `asyncio.gather`, nên batch tốn thời gian của server chậm hơn chứ không phải tổng hai lượt — và cả hai vector được ghi lên cùng một point, nên retrieval hybrid không cần quét file lần hai. Việc đó có xảy ra hay không được quyết định bằng cách hỏi chính *collection* (`supports_sparse()`) sau `ensure_collection`, chứ không tin vào config: collection tạo trước khi bật sparse thì không có field sparse, không backend nào thêm được, và upsert sparse vector vào đó sẽ fail cả batch. Kết quả được ghi vào `metrics["sparse_enabled"]`.
- Lưu ý: stage gộp này không override `observation_type`, nên nó xuất hiện trên Langfuse dưới `ObservationType.SPAN`, khác với `EmbedStage` trước đây (`EMBEDDING`).

`IngestionContext` cũng theo đó: không còn field `embeddings` — vector chỉ tồn tại trong phạm vi một batch, và `num_inserted` là output duy nhất của bước cuối.

`build_ingestion_pipeline(...)` là nơi duy nhất quyết định stage nào chạy và theo thứ tự nào. Nó chỉ cố định splitter khi request có nêu tên; bản thân `ChunkingService` được dựng bên trong `ChunkStage` chứ không phải lúc startup hay theo từng pipeline, vì kích thước đến từ request tạo vector store còn splitter có khi chỉ biết được sau khi document đã được parse.

### Retrieval (`pipelines/retrieval/`)

`RetrievalContext` mang vào query, `limit`, `filters` trung lập và `score_threshold`; mang ra `dense_vector`, `sparse_vector` (chỉ khi hybrid), `candidates` (hit theo tên retriever) và `results`.

| Stage | Làm gì | Ghi chú span |
|---|---|---|
| `EmbedQueryStage` | Embed nội dung query | `ObservationType.EMBEDDING` |
| `RetrieveStage` | Chạy mọi retriever song song, gom hit theo tên retriever | `ObservationType.RETRIEVER`; gộp `span_attributes()` của từng retriever dưới prefix tên riêng |
| `FuseStage` | Trộn các danh sách candidate thành danh sách xếp hạng cuối | bỏ span khi không có gì để trộn |

`BaseRetriever` là seam để gắn hybrid search — `RetrievalQuery` mang cùng lúc mọi biểu diễn của query (text gốc, dense vector, sparse vector), để mỗi retriever tự lấy cái nó hiểu mà pipeline không cần biết đó là cái nào. `DenseRetriever` và `HybridRetriever` là hai implementation; cả hai trả `[]` khi collection chưa tồn tại chứ không raise, vì row của vector store có thể tồn tại trước khi ingestion kịp tạo collection.

`HybridRetriever` là **một** retriever chứ không phải hai: nó đưa cả hai vector cho store trong đúng một lời gọi `retrieve()`, và backend trộn nhánh dense với nhánh sparse ngay phía server bằng reciprocal rank — Qdrant bằng `prefetch` + `FusionQuery(RRF)`, Milvus bằng `hybrid_search` + `RRFRanker`. Nhờ vậy hybrid chỉ tốn một round-trip trên cả hai engine, và hai thang điểm không so sánh được với nhau — cosine và tích vô hướng của term weight — không bao giờ phải quy về cùng thang, chỉ có thứ hạng được trộn. Span attribute `used_sparse` của nó là chỗ cần xem đầu tiên khi kết quả hybrid trông y hệt dense. Hai key trong `config.yaml` điều chỉnh phần fusion, đều ở mức deployment chứ không phải per-request và đều được ghi lên span: `retrieval.hybrid_prefetch_multiplier` (mặc định 2) quyết định mỗi nhánh lấy sâu bao nhiêu trước khi fuse, còn `retrieval.rrf_k` (để trống = mặc định của chính backend, 60 ở cả hai) quyết định việc một doc được cả hai nhánh tìm thấy đáng giá bao nhiêu so với một cú hit mạnh ở một nhánh. Để trống trên Qdrant thì `rrf_k` vẫn gửi đúng `FusionQuery(fusion=RRF)` như trước khi nó chỉnh được; có giá trị thì chuyển sang `RrfQuery(rrf=Rrf(k=…))`, nên chỉ deployment chủ động bật mới gặp dạng request mới hơn.

`BaseFusion` là seam tương ứng cho việc trộn *trong process*. `PassthroughFusion` là implementation duy nhất — mọi search đều chạy đúng một retriever, kể cả hybrid — và sẽ **raise** nếu nhận nhiều hơn một danh sách candidate thay vì âm thầm vứt bớt kết quả. Seam vẫn giữ cho trường hợp khác: một backend không trộn được ở phía server.

`build_retrieval_pipeline(vector_store, embed_fn, search_type, sparse_embed_fn)` phân giải một `SearchType` thành `_RetrievalPlan(retrievers, fusion)`. Retriever và fusion được chọn *cùng nhau* để không thể vô tình ghép ra một tổ hợp sai. Sparse embedder bị giữ lại khi search là dense, để query không bị embed hai lần cho một biểu diễn không ai đọc.

`hybrid_unavailable_reason(vector_store)` là nguồn sự thật duy nhất cho câu hỏi hybrid có chạy được hay không: trả `None` nếu chạy được, ngược lại trả về một câu nêu rõ đang thiếu nửa nào — `SPARSE_EMBEDDING_ENABLED` đang tắt (phía server), hoặc collection không mang sparse vector (phía store). Điều kiện thứ hai không suy ra được từ điều kiện thứ nhất — store đã ingest trước khi bật sparse thì không có field sparse, và không backend nào thêm được field vector vào collection đang sống (cấu hình vector của Qdrant và schema của Milvus đều cố định từ lúc tạo) — nên phải hỏi collection chứ không giả định (`supports_sparse()`: Qdrant đọc cấu hình vector của collection, Milvus đọc `describe_collection`). Nó trả về lý do thay vì một bool bởi hai kiểu thất bại cần hai cách sửa khác nhau: bật sparse, so với ingest lại store.

`resolve_search_type(vector_store)` chỉ là lớp bọc mỏng quanh hàm trên cho caller không có ý kiến riêng: `HYBRID` khi lý do là `None`, còn lại `DENSE`. Phân giải theo từng search chính là thứ cho phép store dense cũ và store hybrid mới cùng được phục vụ bởi một process.

`VectorStoreService.search` chọn theo thứ tự ba nguồn: tham số `search_type` tường minh (caller nội bộ), rồi tới field `search_type` của request (`"auto" | "dense" | "hybrid"`, mặc định `"auto"`), rồi mới tới `resolve_search_type`. `HYBRID` được chỉ định thẳng sẽ bị kiểm tra lại bằng `hybrid_unavailable_reason` và bị từ chối bằng `UnsupportedSearchTypeException` (400) thay vì rơi về dense — âm thầm trả kết quả dense cho một request hybrid sẽ khiến caller đo chất lượng retrieval trên một cấu hình họ không nghĩ là mình đang chạy. Toàn bộ quyết định này diễn ra trước khi span trace được mở, nên trace ghi lại đúng search đã chạy.

Việc resolve search type theo cách này đã gọi `collection_exists()` một lần (qua `supports_sparse()`), và retriever được dựng cho search type đó cũng cần đúng câu trả lời ấy. Thay vì để retriever tự hỏi lại, `search` chỉ check một lần ở đầu (chỉ khi nhánh phía trên thực sự cần — search pin dense bỏ qua bước này, để retriever tự check như cũ) rồi truyền kết quả xuyên suốt qua `resolve_search_type`/`hybrid_unavailable_reason`/`supports_sparse` và vào `DenseRetriever`/`HybridRetriever` dưới dạng tham số tuỳ chọn — bớt một round-trip tới backend trên đường đi phổ biến, mà không đụng tới lần check lại (mục đích khác hẳn) của `ensure_collection()` lúc ingest.

`chunks_to_trace_json` chỉ render hit thành `{chunk_id, score}` cho span attribute: trace không phải nơi để nhân bản nội dung tài liệu.

## Component layer (`app/components/`)

Mọi package ở đây theo cùng một khuôn — một `base.py` khai báo interface, một `provider/` chứa implementation, và một facade service có `from_settings()` chọn một cái từ config. Không có gì ở đây điều phối hay biết tới HTTP.

| Package | Interface | Provider | Chọn bằng |
|---|---|---|---|
| `parsing/` | `BaseParsingProvider` | `LlamaParseProvider` (`.pdf`), `UnstructuredProvider` (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) | `PDF_PARSER_PROVIDER` (chỉ cho PDF) |
| `chunking/` | `BaseChunkingProvider` | `ChonkieProvider`, `LangchainProvider` | `chunking_splitter` trong request, hoặc splitter tự suy ra từ tài liệu — tra qua `registry.py` |
| `embedding/` | `BaseEmbeddingProvider` | `OpenAIEmbeddingProvider`, `TEIEmbeddingProvider` | `EMBEDDING_PROVIDER` |
| `embedding/` | `BaseSparseEmbeddingProvider` | `VLLMSparseEmbeddingProvider` | `SPARSE_EMBEDDING_PROVIDER` (chỉ khi `SPARSE_EMBEDDING_ENABLED`) |

Cả hai provider embedding đều chỉ là HTTP client — không model nào được nạp trong tiến trình này, nên cả web service lẫn worker đều không cần GPU. `OpenAIEmbeddingProvider` gọi `{DENSE_EMBEDDING_URL}/embeddings`, `TEIEmbeddingProvider` gọi `{DENSE_EMBEDDING_URL}/embed`, còn `VLLMSparseEmbeddingProvider` gọi `/tokenize` + `/pooling` trên root của sparse. Thứ trả lời các URL đó là một deployment riêng: [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) là bản tham chiếu — vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B` trên `:8100` và `BAAI/bge-m3` trên `:8101`, đúng bằng giá trị mặc định mà các setting này mang sẵn.

`ParsingService.from_settings()` map extension tới *provider factory*, không phải instance: `.pdf` đi tới backend được `PDF_PARSER_PROVIDER` đặt tên, **mọi định dạng còn lại** (`.txt`, `.md`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg`) đi tới Unstructured API. Vì `.txt`/`.md` được đăng ký cùng *một object factory*, chúng dùng chung một instance provider — dict `_instances` được key theo factory chứ không theo extension chính vì thế. Tên backend PDF được kiểm tra lúc startup (nên gõ sai là fail ngay) nhưng provider chỉ được khởi tạo khi thực sự có file cần parse, và `UNSTRUCTURED_API_KEY` cũng chỉ được kiểm tra ở thời điểm đó — một deployment chỉ ingest PDF không nên fail khi khởi động vì thiếu key của Unstructured. `supports()`, `supported_extensions` và `provider_for()` phơi bày registry; extension không có trong map sẽ raise `ValueError("Unsupported file format: ...")`.

`UnstructuredProvider` gọi `partition_via_api` (thư viện `unstructured` là đồng bộ, nên chạy qua `asyncio.to_thread`) và nhận về element list JSON — API này **không** hỗ trợ output Markdown, chỉ `application/json` hoặc `text/csv`. Provider tự render element list thành Markdown tại chỗ: `Title` → heading theo `category_depth`, `ListItem` → bullet thụt lề, `Table` → bảng Markdown dựng từ metadata `text_as_html` (qua BeautifulSoup). Nhờ vậy mọi provider parsing đều trả về cùng một hình dạng output (Markdown), giống `LlamaParseProvider` với PDF — chunking phía sau không phải phân biệt định dạng nguồn.

Lưu ý điểm lệch giữa hai allow-list: `ALLOWED_EXTENSIONS` lúc upload cho phép `.csv`, `.json` và `.gif` — ba định dạng đó **upload được** dưới dạng File nhưng không có provider parsing nào đăng ký, nên ingestion sẽ raise `ValueError`. Ngược lại, `.md` và `.doc` **parse được** nhưng không nằm trong `ALLOWED_EXTENSIONS`, và `validate_file_type` chặn theo extension (không có ngoại lệ), nên chúng luôn bị 415 ngay ở bước upload.

`ChunkingService.for_strategy(strategy, chunk_size, chunk_overlap)` dựng splitter cho một strategy thông qua `registry.py`, và phơi bày `chunk_size`, `chunk_overlap` cùng `async split_text(text)`. Có năm strategy được đăng ký, mỗi cái ứng với đúng một implementation: `recursive`, `token` và `sentence` trên Chonkie, `character` và `markdown` trên `langchain_text_splitters`. Registry là một hàm toàn phần trên enum, kiểm tra ngay lúc import — thêm một giá trị mà quên entry sẽ fail lúc startup thay vì `KeyError` trong worker.

Cái nào chạy thì hoặc do request nêu tên (`chunking_splitter`), hoặc do `detection.py` đọc ra từ document: `markdown` khi file upload là `.md`/`.markdown` hoặc text đã parse có ít nhất hai heading `h1`–`h4`, còn lại là `recursive`. Splitter markdown cắt theo heading trước, gắn vào đầu mỗi chunk đường dẫn heading của nó (`Guide > Billing > Refunds`) và trừ phần prefix đó vào ngân sách chunk, rồi đưa những section vẫn còn quá dài qua một lượt recursive.

Kích thước là chuyện độc lập: `chunking_strategy` (`"auto"`/`"static"`) chỉ quyết định `chunk_size`/`chunk_overlap`, và với `"auto"` thì không ghim con số nào cả, nên mặc định riêng của từng strategy được áp dụng — 800/400 cho các splitter theo cửa sổ, 1200/120 cho markdown vì chunk của nó là nguyên một section theo heading chứ không phải cửa sổ cố định. `recursive` hoàn toàn không có núm overlap (`ChunkingStrategy.supports_overlap`), nên xin overlap kèm `chunking_splitter="recursive"` tường minh sẽ nhận 422 chứ không bị bỏ lặng lẽ; overlap bằng hoặc vượt `chunk_size` bị chặn xuống còn một nửa thay vì raise trong worker. `tokenizer="character"` ở mọi nơi nghĩa là `max_chunk_size_tokens` vẫn được đếm theo ký tự chứ không phải token (xem [Quyết định thiết kế](DESIGN_DECISIONS_vi.md#giới-hạn-đã-biết)). Những gì thực sự đã chạy được ghi ngược lại vào context, báo trên span `chunk`, và merge vào `chunking_strategy.resolved` của vector store.

Cả hai chunking provider đều `run_in_executor(get_cpu_executor(), ...)`: splitting là CPU-bound và nếu chạy thẳng trên event loop sẽ làm đứng mọi task khác trong cùng tiến trình. Pool CPU (`CPU_THREAD_POOL_SIZE`, mặc định 4) được cố ý tách khỏi pool I/O (`IO_THREAD_POOL_SIZE`, mặc định 32) — oversubscribe công việc CPU-bound chỉ thêm context switch chứ không tăng thông lượng, còn dùng chung pool thì một lượt transfer MinIO chậm có thể làm chunking phải xếp hàng, và ngược lại.

## Data layer (`app/db/`)

| Package | Class | Phục vụ | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Byte của file đã upload (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | Bảng metadata `files` + `vector_stores` (sở hữu theo `api_key`, status, `vector_store_type`, metadata JSONB) | `postgres` (5432) |
| `vector_store/` | `BaseVectorStoreConnection`, `BaseAsyncVectorStore`, `VectorStoreFactory` | Mỗi vector store một collection (`collection_name == vector_store_id`) | không có — dịch vụ ngoài (Qdrant `6333`, Milvus `19530`), nằm ngoài stack Compose này |

Mọi thao tác MinIO (`upload_file`, `download_file`, `delete_file`) đều nhận tham số `executor` và được gọi với `get_io_executor()`, thay vì mượn default executor của event loop. `download_file` uỷ quyền cho helper `_fetch_object`, gộp `get_object()` + `.read()` + `close()` thành **một** đơn vị công việc trên worker thread: `get_object()` chỉ mở stream (đọc header), toàn bộ transfer thật nằm ở `.read()`, nên nếu chỉ offload lời gọi mở stream thì event loop vẫn bị chặn theo kích thước file.

### Schema Postgres (`db/postgres/schema/`)

Hai bảng và hai index, tất cả đều `CREATE ... IF NOT EXISTS` và được `_create_table()` áp dụng — hàm này **chỉ** chạy trên tiến trình web. `init_postgres()` phía worker chỉ dựng pool rồi dừng, nên web service phải boot ít nhất một lần trước khi worker ghi được.

| Bảng | Cột | Ghi chú |
|---|---|---|
| `files` | `id` (PK, `file-{8 hex}`), `api_key`, `bytes`, `purpose`, `created_at`, `expires_at`, `content_type`, `metadata` JSONB | `metadata` giữ `{filename, minio_bucket, minio_path, etag}` — con trỏ ngược về object, và đó là lý do xoá row mà không xoá object sẽ để lại rác |
| `vector_stores` | `id` (PK, `vs_{32 hex}`), `api_key`, `name`, `description`, `created_at`, `last_active_at`, `status`, `usage_bytes`, `metadata` JSONB, `expires_at`, `expires_after`, `chunking_strategy` JSONB, `vector_store_type` | `status` là trạng thái của cả store (`in_progress`/`completed`/`failed`), còn `vector_store_type` chính là thứ cho phép kết nối nhiều backend cùng lúc — mỗi row tự ghi engine đang giữ collection của nó |

Cả hai index đều là index tổ hợp và được xếp thứ tự khớp đúng với truy vấn list: `idx_files_api_key_purpose_created_at_id` trên `(api_key, purpose, created_at, id)` và `idx_vector_stores_api_key_created_at_id` trên `(api_key, created_at, id)`. Quyền sở hữu đứng trước vì mọi truy vấn đều lọc theo `api_key`, còn `created_at, id` là khoá sắp xếp của cursor pagination — `id` để phá thế hoà, để hai row tạo cùng một thời điểm không bị trả hai lần hay bị bỏ sót. Thêm một bộ lọc list không phải tiền tố của hai index này thì bộ lọc đó chạy không index.

Không có bảng `vector_store_files`: quan hệ store–file chỉ tồn tại trong tham số của task ingestion, và đó mới là lý do cấu trúc khiến ingest nhiều file không đơn thuần là bỏ một lệnh kiểm tra (xem [Quyết định thiết kế](DESIGN_DECISIONS_vi.md#giới-hạn-đã-biết)).

### Trừu tượng hoá vector store (`db/vector_store/`)

Hai abstraction trong `base.py`:

- `BaseVectorStoreConnection` — kết nối dài hạn tạo một lần lúc startup (`from_settings()`, `client`, `check_connection()`, `close()`); tương đương một connection pool
- `BaseAsyncVectorStore` — các thao tác giới hạn trong một collection, dựng theo từng lần dùng từ một client

`ensure_collection(embedding_dim, with_sparse)` được tách khỏi `insert_documents` một cách có chủ đích. Gộp việc tạo collection vào đường insert buộc mọi batch song song phải tranh nhau một check-then-act — đó chính là lý do code ingest trước đây phải chạy batch đầu tiên một mình. Giờ caller tạo collection một lần từ đầu, sau đó mọi insert đều thuần và song song an toàn. Cả hai implementation vẫn chấp nhận thua trong cuộc đua đó — nếu tạo thất bại mà collection lúc này đã tồn tại thì tiến trình khác đã thắng, lời gọi trả `False` thay vì raise, nên hai worker cùng khởi động một store không làm nhau fail.

Điểm quan trọng đi kèm: `embedding_dim` truyền vào đến từ `get_dense_embedding_dim()` (cache lúc startup) chứ không phải từ vector embed đầu tiên. Nhờ vậy contract của vector store không còn ép caller phải embed trước khi tạo collection — chính điều đó cho phép `EmbedAndIndexStage` vừa embed vừa ghi theo kiểu streaming.

`types.py` chứa các hình dạng trung lập với backend — `RetrievedChunk`, và cây filter gồm `FieldCondition` / `FilterGroup` với `FilterOperator` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`) và `FilterCombinator` (`and`, `or`). Không gì trong module này được phép import SDK của nhà cung cấp; đây là những hình dạng duy nhất tầng service nhìn thấy, nên việc đổi Qdrant sang Milvus không bao giờ lan lên trên `app.db`.

`VectorStoreFactory` phân giải tên provider thành implementation. Backend được định địa chỉ bằng *đường dẫn module* và import lúc dùng lần đầu chứ không phải ở mức module — điều đó giữ đồ thị import không có chu trình, và nghĩa là một backend chưa cài SDK chỉ fail khi thực sự bị yêu cầu. Startup đẩy connection sống xuống qua `register_connection()`; factory không bao giờ import `app.startup`. `get_store(collection_name, provider)` nhận provider ghi trên row của vector store; `get_connection` raise `RuntimeError` có nêu cách sửa khi một store tham chiếu tới provider mà deployment này không chạy.

Mỗi backend cung cấp một `filter_translator.py` để render cây trung lập sang ngôn ngữ của chính nó (`to_qdrant_filter`, `to_milvus_expression`).

**Qdrant** (`provider/qdrant/`) — `AsyncQdrantVectorStore` tạo collection với HNSW + quantization cấu hình được (`scalar`/`binary`/`product`), `indexing_threshold=0` lúc tạo rồi nâng lên `20000` sau khi insert hàng loạt để tránh chi phí indexing giữa chừng. `retrieve` chạy một truy vấn cho mỗi query vector, gom song song, và trả về `RetrievedChunk`. Tên các trường vector lấy từ `DENSE_VECTOR_NAME` / `SPARSE_VECTOR_NAME`, tức là model id đã đi qua `_vector_name()` — Qdrant cấm ký tự `/` và `:` trong tên vector mà model id nào cũng có `/`, nên `Qwen/Qwen3-Embedding-0.6B` được lưu thành `Qwen_Qwen3-Embedding-0.6B`. Đổi quy tắc ánh xạ này sẽ làm mọi collection tạo trước đó thành vô dụng, vì `using=` không còn khớp trường nào đang tồn tại.

**Milvus** (`provider/milvus/`) — `AsyncMilvusVectorStore` tạo collection với HNSW trên trường `dense_vector`, cộng thêm `SPARSE_INVERTED_INDEX` trên `sparse_vector` khi bật sparse; mỗi document là một row mang `page_content` và một trường JSON `metadata`, nên payload vẫn tương thích với backend Qdrant. `retrieve` gửi mọi query vector trong đúng một request (`search`, hoặc `hybrid_search` với `RRFRanker` khi có sparse vector), khác với kiểu fan-out song song của Qdrant. Hai khác biệt của engine được hấp thụ ngay tại đây thay vì lộ ra ngoài: tên được gấp về dạng Milvus chấp nhận — id hiện tại (`vs_a1b2…`) đi thẳng thành tên collection không đổi, còn id gạch ngang cũ (`vs-a1b2…`) vẫn gấp thành `vs_a1b2…`, và trường vector dùng tên cố định thay vì model id vốn bị Milvus từ chối — và một search gặp collection chưa load (như sau khi server khởi động lại) sẽ tự load rồi thử lại một lần. Đọc ở mức `Strong` để khớp read-your-writes của Qdrant.

## Cấu hình (`app/core/config/`)

Hai lớp được gộp theo từng module domain:

1. **pydantic-settings** (`settings.py`) — đọc `.env` / biến môi trường thật. Các field bắt buộc (không có default) là API key và credential Postgres/MinIO/Langfuse; credential của vector store thì **bắt buộc có điều kiện**, do `validate_vector_store_credentials` kiểm tra riêng cho `VECTOR_STORE_PROVIDER` (`QDRANT_URL` + `QDRANT_API_KEY`, hoặc `MILVUS_URI`) — deployment chỉ chạy Qdrant không bao giờ phải điền phần Milvus, và chính việc điền credential của một backend là thứ kết nối nó (`enabled_vector_store_providers`). Validator bắt buộc `API_VERSION` bắt đầu bằng `v`, port dương, secret không rỗng, `LOG_LEVEL`/`LOG_FORMAT` nằm trong tập cho phép, và mỗi biến `EMBEDDING_PROVIDER` / `SPARSE_EMBEDDING_PROVIDER` / `PDF_PARSER_PROVIDER` / `VECTOR_STORE_PROVIDER` phải là một backend đã biết — nên gõ sai sẽ fail lúc startup, không phải lúc dùng lần đầu. `_VECTOR_STORE_REQUIRED_SETTINGS` là một dict duy nhất vừa làm tập giá trị hợp lệ vừa làm danh sách credential theo backend, nên thêm một backend chỉ sửa một chỗ.
2. **YAML** (`config/config.yaml`, nạp qua `YamlConfigLoader`) — các tunable ổn định: `api.num_workers`, `storage.uploaded_file_bucket`/`parsed_text_bucket`/`max_file_size`/`io_thread_pool_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`, `ingestion.cpu_thread_pool_size`/`storage_concurrency`, `retrieval.hybrid_prefetch_multiplier`/`rrf_k`. Hai key retrieval được kiểm tra ngay lúc **import** (`retrieval.py` raise nếu không phải số nguyên hoặc nhỏ hơn 1) chứ không qua pydantic, vì chúng không bao giờ đến từ biến môi trường.

Các module theo domain (`database.py`, `storage.py`, `models.py`, `embedding.py`, `ingestion.py`, `redis.py`, `langfuse.py`, `api.py`) gộp cả hai nguồn thành hằng số phẳng import được từ `app.core.config`. Ba tham số điều tiết concurrency của worker nằm ở hai module khác nhau vì thuộc hai domain khác nhau: `IO_THREAD_POOL_SIZE` trong `storage.py` (nó phục vụ MinIO), còn `CPU_THREAD_POOL_SIZE` và `STORAGE_CONCURRENCY` trong `ingestion.py`. Danh sách tham số đầy đủ: [Configuration Reference](../CONFIGURATION.md).

## Tracing (`app/core/tracing/`)

Langfuse qua exporter **OpenTelemetry OTLP** — `init_tracing()` dựng `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter(...))` trỏ tới `{LANGFUSE_BASE_URL}/api/public/otel/v1/traces` với Basic-Auth từ cặp public/secret key.

- `tracing.py` — context manager `traced_span(name, attributes, parent_carrier)` (tự đặt `Status.OK`/`Status.ERROR`, ghi lại exception) và `set_span_attributes()`
- `attributes.py` — tên attribute của Langfuse dưới dạng hằng số (`TRACE_INPUT`, `TRACE_TAGS`, `TRACE_USER_ID`, `OBSERVATION_TYPE`, …), enum `ObservationType` (`SPAN`, `EMBEDDING`, `RETRIEVER`, …), và các helper `observation_metadata()` / `trace_metadata()` tự loại bỏ giá trị `None`
- `propagation.py` — `inject_trace_context()` / `extract_trace_context()` trên một `TraceCarrier` (W3C traceparent), thứ cho phép pipeline phía worker lồng vào trace của HTTP request

Cấu trúc span: `VectorStoreService.search` mở root span (`POST /v1/vector_stores/{id}/search`) mang các attribute mức trace, và `RetrievalPipeline` phát ra một observation cho mỗi stage bên trong đó. Ingestion phản chiếu điều này từ phía worker, với cha là carrier được truyền sang. Cả web app lẫn `app/tasks/broker.py` đều gọi `init_tracing()` độc lập lúc startup của mình.

## Exceptions (`app/exceptions/`)

Gốc là `AppBaseException(status_code, response: BaseResponse, log_message)`. Các subclass theo domain: `auth/` (`APIKeyIncorrectException`, `BearerMissingException` — 401), `file/` (`FileSizeLimitExceededException` 413, `FileNotFoundException` 404), `postgres/` (`PostgresConnectionException` 503), `vector_store/` (`VectorStoreNotFoundException` 404, `WrongPrefixVectorstoreException` 400, `UnsupportedMultipleFilesException` 400). `common_exception_handler` (đăng ký toàn cục) log ở mức ERROR/WARNING tuỳ status và trả `JSONResponse({message, type, params, code})` — error envelope kiểu OpenAI. `HTTPException` thuần (ví dụ 415 từ `app/api/dependencies.py`) rơi xuống handler mặc định của FastAPI.

## Schemas (`app/schemas/`)

- `base/` — `BaseModel` dùng chung (`extra="forbid"`), `PaginationParams`, `PaginatedResponse[T]` generic.
- `file/` — `FileObject`, `FileListObject`, các biến thể request/response, enum `UploadingStatus`.
- `vector_store/` — `VectorStoreCreateRequest`/`ModifyRequest`/`QueryRequest`/`SearchRequest`, các union type chunking-strategy, `ComparisonFilter`/`CompoundFilter`, `VectorStoreObject`, `VectorStoreSearchResponse`.
- `vector_store/types.py` — `VectorStoreType` (`qdrant`, `milvus`) và `SearchType` (`dense`, `hybrid`; caller chỉ định thẳng, hoặc gửi `"auto"` để phân giải theo từng search). `SearchType` đặt tên cho *toàn bộ* hình dạng retrieval thay vì phơi riêng danh sách retriever và chiến lược fusion, vì hai thứ đó luôn phải khớp nhau — đặt tên cho tổ hợp khiến các trạng thái sai không biểu diễn được.
- `chunking/` — enum `ChunkingStrategy`, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Startup / bootstrap (`app/startup.py`)

Các biến global kiểu service-locator, đặt bởi `init_embed_model`, `init_parsing_service`, `init_postgres`, `init_minio`, `init_vector_store`, `init_io_executor`, `init_cpu_executor`, `init_storage_semaphore` và đọc qua các getter tương ứng (`get_dense_embedding`, `get_dense_embedding_dim`, `get_parsing_service`, `get_postgres_pool`, `get_postgres_client`, `get_minio_service`, `get_vector_store_connection`, `get_io_executor`, `get_cpu_executor`, `get_storage_semaphore`).

`init_embed_model` dựng `EmbeddingService.from_settings()` cho provider đã cấu hình rồi smoke-test nó; chính lời gọi `check_connection()` đó cache luôn số chiều vector, sau này đọc qua `get_dense_embedding_dim()` (raise `RuntimeError` nếu bị gọi trước khi init). `init_sparse_embed_model` làm y hệt cho phía lexical — cũng dựng-rồi-probe, nên sparse server không truy cập được sẽ fail lúc boot chứ không phải lúc ingest lần đầu — chỉ khác ở chỗ nó là opt-in: khi `SPARSE_EMBEDDING_ENABLED` false thì nó chỉ log và để service ở `None`, và `get_sparse_embed_model()` sẽ raise `RuntimeError` (dùng `is_sparse_embedding_enabled()` nếu muốn rẽ nhánh theo tình trạng có/không). `init_parsing_service` dựng các provider một lần để client của backend parsing được tái sử dụng qua nhiều file thay vì dựng lại mỗi lần ingest. `init_vector_store` dựng connection cho từng backend đã điền credential, kiểm tra từng cái, rồi đăng ký với `VectorStoreFactory` — kết nối nhiều hơn một chính là thứ cho phép phục vụ song song các store nằm trên những engine khác nhau. Chỉ `VECTOR_STORE_PROVIDER` không kết nối được mới chặn boot; backend còn lại bị bỏ qua kèm cảnh báo, vì nó được kết nối chỉ nhờ có sẵn credential. `wait_for_postgres` thử lại pool 5 lần cách nhau 0.5s và raise lại lỗi cuối cùng thay vì chạy tiếp như thể Postgres vẫn truy cập được.

Ba resource điều tiết concurrency cũng nằm ở đây: `init_io_executor` (pool cho I/O blocking của MinIO), `init_cpu_executor` (pool cho chunking CPU-bound và băm sha256) và `init_storage_semaphore` (trần số thao tác MinIO song song mỗi tiến trình worker).

Được dùng giống hệt nhau — nhưng khởi tạo độc lập — bởi cả `app/app.py` (web) và `app/tasks/broker.py` (worker), nên không có biến global riêng cho worker. Hai tiến trình khởi tạo **không hoàn toàn cùng một tập**: web bỏ qua `init_parsing_service`, `init_cpu_executor` và `init_storage_semaphore` vì nó không parse cũng không chunk — nó chỉ cần pool I/O cho upload/delete MinIO.

## Background worker (`app/tasks/`)

`broker.py` chỉ nắm vòng đời của broker: `RedisStreamBroker` + `RedisAsyncResultBackend` trên `REDIS_URL`, với các hook `WORKER_STARTUP`/`WORKER_SHUTDOWN` chạy `_initialize_services()` đúng một lần (chốt bằng cờ `_initialized`), rồi đóng pool Postgres, HTTP client của dense/sparse embedding provider, cùng `VectorStoreFactory.close_all()` khi thoát. `_initialize_services()` phản chiếu nửa khởi động trong lifespan của `app.app` nhưng thêm ba thứ tiến trình web không cần — `init_cpu_executor()`, `init_storage_semaphore()` và `init_parsing_service()` — vì worker là nơi chạy toàn bộ ingestion pipeline. Việc tách task sang module riêng là thứ cho phép `app.tasks.broker:broker` làm entrypoint deploy mà không phải import cả ingestion pipeline chỉ để khởi động tiến trình.

`ingestion_task.py` chứa task duy nhất, `ingest_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunking_splitter, chunk_size, chunk_overlap, request_id, trace_context, vector_store_type)`. Nó chỉ điều phối: bind `request_id_ctx`, uỷ quyền cho `IngestionService`, log rồi raise lại để TaskIQ thấy được lỗi, cuối cùng reset contextvar.

Xem [Flow](FLOW_vi.md) để có sequence diagram đầy đủ.
