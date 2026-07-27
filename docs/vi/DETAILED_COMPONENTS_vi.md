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
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters` đã áp dụng, `ranking_options` thì chưa |

### `dependencies.py`

`validate_file_size` (từ chối file quá `MAX_FILE_SIZE` MB), `validate_file_type` (kiểm tra MIME + extension + tính nhất quán giữa chúng qua `MIME_TYPE_MAPPING`), ghép lại thành `validate_file` — dùng như `Depends(validate_file)` trên endpoint upload.

### `security.py`

`verify_api_key` yêu cầu header `Authorization: Bearer <token>`, so sánh token với `FASTAPI_API_KEY` duy nhất được cấu hình, rồi trả token về dưới dạng `api_key` — giá trị này sau đó dùng để scope các row trong Postgres. Vì chỉ có đúng một token hợp lệ, đây thực chất là auth single-tenant, dù schema được thiết kế theo hướng multi-tenant (xem [Design Decisions](DESIGN_DECISIONS_vi.md)).

### `middleware.py`

`RequestIDMiddleware` là middleware ASGI thuần (không phải `BaseHTTPMiddleware` — loại đó sẽ reset contextvar trước khi access logger của uvicorn kịp chạy). Nó tái sử dụng header `X-Request-Id` do client gửi nếu có, nếu không thì sinh `req_{uuid4().hex}`, bind vào `request_id_ctx` (một `contextvars.ContextVar` trong `app/core/request_context.py`) trong suốt vòng đời request, và echo lại trên response. Chính ID đó được truyền vào `ingest_vector_store_files.kiq(...)` và bind lại bên trong worker task, nên một request ID xuyên suốt cả log HTTP lẫn job ingestion bất đồng bộ mà request đó kích hoạt.

`app/app.py` còn định nghĩa `GET /health` (loại khỏi OpenAPI schema), đăng ký `RequestIDMiddleware`, handler `AppBaseException` toàn cục, và một startup event khởi tạo theo thứ tự tracing → embed model → Postgres (pool + `wait_for_postgres` + tạo bảng) → vector store → MinIO.

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Các static method `upload_file`, `list_files`, `get_file_by_id`, `delete_file`. Luồng upload: sinh `file_id`, dựng object path `{api_key}/uploads/{uuid}_{filename}`, upload byte lên MinIO, rồi insert row metadata vào Postgres. Nếu insert Postgres thất bại sau khi upload MinIO đã thành công, object bị bỏ lại thành mồ côi (chỉ log warning, không dọn dẹp) và `PostgresConnectionException` được raise.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Các static method `create`, `list`, `get`, `modify`, `delete`, `search`.

- `create` từ chối trường hợp nhiều hơn một `file_id` trước cả khi chạm database (`UnsupportedMultipleFilesException`, 400 — "từ chối ngay thay vì để caller poll một store không bao giờ hoàn thành"), ghi row Postgres kèm provider mà nó được tạo trên đó, rồi đẩy `ingest_vector_store_files.kiq(...)` cùng `request_id` và `inject_trace_context()`.
- `search` đọc row để biết `vector_store_type`, chuyển `search_request.filters` thành cây trung lập qua `normalize_search_filter`, mở root trace span, và chạy `RetrievalPipeline` ngay trong tiến trình. Kết quả trả về dưới dạng `RetrievedChunk` rồi được `convert_retrieved_chunks_to_search_results` chuyển sang hình dạng API.
- Cả hai đường đều lấy store qua `VectorStoreFactory.get_store(collection_name, provider)` — truyền vào provider ghi trên row, nên collection tạo dưới một `VECTOR_STORE_PROVIDER` cũ vẫn hoạt động.

### `IngestionService` (`ingestion/ingestion_service.py`)

Business logic của task nền, cố ý không import TaskIQ để có thể gọi và test mà không cần broker đang chạy. `ingest_vector_store_files(...)`:

1. `PostgresFileStore.check_existing_files` — nếu không file nào còn tồn tại, nhảy thẳng tới bước đánh dấu store `completed` với `usage_bytes=0`
2. Lấy `get_total_bytes` + `get_metadata_for_files`
3. Chốt chặn: đúng một file, nếu không thì log, `_mark_failed`, và raise — lớp phòng thủ thứ hai sau lần từ chối ở tầng API, để một store rỗng không bao giờ bị báo `completed`
4. `_ingest_single_file` dựng pipeline qua `build_ingestion_pipeline(...)` và chạy nó trên một `IngestionContext`, truyền `trace_context` làm parent carrier
5. Đánh dấu `completed` kèm `usage_bytes`; mọi lỗi đều đánh dấu `failed` rồi raise lại

## Pipeline layer (`app/pipelines/`)

### Khung (`base.py`, `pipeline.py`)

`BaseStage[ContextT]` là contract: một `name` (tên span), một `observation_type`, một `async run(context)`, cộng hai hook tuỳ chọn — `emits_span(context)` (quyết định *trước* `run`, nên có thể nhìn vào kết quả của stage trước; trả `False` vẫn chạy stage, chỉ là giữ một no-op ra khỏi trace) và `span_attributes(context)` (đọc *sau* khi `run` thành công).

`Pipeline[ContextT]` chạy các stage theo thứ tự dưới một span cha duy nhất và là **nơi duy nhất mở span**. Stage không bao giờ import module tracing. Subclass override `root_attributes()` (đặt trước mọi stage) và `result_attributes()` (đặt sau khi mọi stage thành công). `run(context, parent_carrier)` nhận một `TraceCarrier` để pipeline khởi chạy từ một job đã enqueue xuất hiện bên trong trace của request đã enqueue nó.

Lợi ích: hình dạng trace là thuộc tính của pipeline, và nó vẫn đúng khi stage được thêm, bớt, hay đảo thứ tự.

### Ingestion (`pipelines/ingestion/`)

`IngestionContext` là một dataclass mutable duy nhất xuyên suốt mọi stage — input (`vector_store_id`, `file_id`, `file_metadata`, tham số chunking), kết quả điền dần (`raw_bytes` → `text` → `chunks` → `embeddings` → `num_inserted`), và một dict `metrics` tự do cho những con số thuộc về span nhưng không thuộc về state của pipeline. Các property tiện lợi (`filename`, `file_extension`, `storage_bucket`, `storage_path`) đọc từ `file_metadata`.

| Stage | Làm gì | Ghi chú span |
|---|---|---|
| `DownloadStage` | Lấy byte từ MinIO | — |
| `ParseStage` | `ParsingService.parse(bytes, ext)` → text | ghi lại provider đã xử lý |
| `ChunkStage` | `ChunkingService.split_text(text)` → chunks | — |
| `EmbedStage` | Chia chunk thành batch, embed song song | `ObservationType.EMBEDDING`; báo cáo `num_batches`, `batch_size` |
| `IndexStage` | `ensure_collection()` một lần, rồi insert các batch song song | báo cáo collection có được tạo mới không, `num_batches` |

Cả `EmbedStage` và `IndexStage` đều chia thành batch `EMBEDDING_UPLOAD_BATCH_SIZE` và giới hạn bằng `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY)`, nên bộ nhớ đỉnh xấp xỉ `batch_size * concurrency` bất kể file lớn cỡ nào.

`build_ingestion_pipeline(...)` là nơi duy nhất quyết định stage nào chạy và theo thứ tự nào. `ChunkingService` được dựng theo từng pipeline chứ không phải lúc startup, vì chunk size và overlap đến từ request tạo vector store.

### Retrieval (`pipelines/retrieval/`)

`RetrievalContext` mang vào query, `limit`, `filters` trung lập và `score_threshold`; mang ra `dense_vector`, `candidates` (hit theo tên retriever) và `results`.

| Stage | Làm gì | Ghi chú span |
|---|---|---|
| `EmbedQueryStage` | Embed nội dung query | `ObservationType.EMBEDDING` |
| `RetrieveStage` | Chạy mọi retriever song song, gom hit theo tên retriever | `ObservationType.RETRIEVER`; gộp `span_attributes()` của từng retriever dưới prefix tên riêng |
| `FuseStage` | Trộn các danh sách candidate thành danh sách xếp hạng cuối | bỏ span khi không có gì để trộn |

`BaseRetriever` là seam cho hybrid search — `RetrievalQuery` cố ý mang cả nội dung query gốc *lẫn* dense vector, để một retriever tự tokenise có đủ thứ nó cần mà pipeline không phải biết retriever nào dùng biểu diễn nào. `DenseRetriever` là implementation duy nhất; collection không tồn tại thì trả `[]` chứ không raise, vì row của vector store có thể tồn tại trước khi ingestion kịp tạo collection.

`BaseFusion` là seam tương ứng cho việc trộn. `PassthroughFusion` là implementation duy nhất và sẽ **raise** nếu nhận nhiều hơn một danh sách candidate, thay vì âm thầm vứt bớt kết quả — thêm retriever thứ hai buộc bạn phải chọn một chiến lược fusion thật.

`build_retrieval_pipeline(vector_store, embed_fn, search_type)` phân giải một `SearchType` thành `_RetrievalPlan(retrievers, fusion)`. Retriever và fusion được chọn *cùng nhau* để không thể vô tình ghép ra một tổ hợp sai. Search type là tham số theo từng lời gọi chứ không phải cấu hình — hai query trên cùng một store hoàn toàn có thể muốn kiểu retrieval khác nhau.

`chunks_to_trace_json` chỉ render hit thành `{chunk_id, score}` cho span attribute: trace không phải nơi để nhân bản nội dung tài liệu.

## Component layer (`app/components/`)

Mọi package ở đây theo cùng một khuôn — một `base.py` khai báo interface, một `provider/` chứa implementation, và một facade service có `from_settings()` chọn một cái từ config. Không có gì ở đây điều phối hay biết tới HTTP.

| Package | Interface | Provider | Chọn bằng |
|---|---|---|---|
| `parsing/` | `BaseParsingProvider` | `TextProvider` (`.txt`, `.md`), `LlamaParseProvider` (`.pdf`) | `PDF_PARSER_PROVIDER` |
| `chunking/` | `BaseChunkingProvider` | `ChonkieProvider`, `LangchainProvider` | `CHUNKING_PROVIDER` |
| `embedding/` | `BaseEmbeddingProvider` | `OpenAIEmbeddingProvider`, `TEIEmbeddingProvider` | `EMBEDDING_PROVIDER` |

`ParsingService.from_settings()` map extension tới *provider factory*, không phải instance: định dạng text thuần luôn dùng decoder in-process, còn backend PDF được kiểm tra tên lúc startup (nên gõ sai là fail ngay) nhưng chỉ được khởi tạo khi thực sự có PDF cần parse. `supports()`, `supported_extensions` và `provider_for()` phơi bày registry; extension không có trong map sẽ raise `ValueError("Unsupported file format: ...")`.

Lưu ý điểm lệch: `ALLOWED_EXTENSIONS` lúc upload còn cho phép `.docx`, `.csv`, `.json` và ảnh — những định dạng đó **upload được** dưới dạng File nhưng không có provider parsing nào đăng ký.

`ChunkingService` phơi bày `strategy_name` và `async split_text(text)`. Provider Chonkie hỗ trợ `character | sentence | recursive | token` (mặc định `recursive`, `chunk_size=800`, `chunk_overlap=400`). Ở tầng API, `VectorStoreCreateRequest.chunking_strategy` chỉ cho chọn `"auto"` hoặc `"static"`, nên các strategy chi tiết hơn không tiếp cận được qua API công khai.

## Data layer (`app/db/`)

| Package | Class | Phục vụ | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Byte của file đã upload (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | Bảng metadata `files` + `vector_stores` (sở hữu theo `api_key`, status, `vector_store_type`, metadata JSONB) | `postgres` (5432) |
| `vector_store/` | `BaseVectorStoreConnection`, `BaseAsyncVectorStore`, `VectorStoreFactory` | Mỗi vector store một collection (`collection_name == vector_store_id`) | `qdrant` (6333/6334) |

### Trừu tượng hoá vector store (`db/vector_store/`)

Hai abstraction trong `base.py`:

- `BaseVectorStoreConnection` — kết nối dài hạn tạo một lần lúc startup (`from_settings()`, `client`, `check_connection()`, `close()`); tương đương một connection pool
- `BaseAsyncVectorStore` — các thao tác giới hạn trong một collection, dựng theo từng lần dùng từ một client

`ensure_collection` được tách khỏi `insert_documents` một cách có chủ đích. Gộp việc tạo collection vào đường insert buộc mọi batch song song phải tranh nhau một check-then-act — đó chính là lý do code ingest trước đây phải chạy batch đầu tiên một mình. Giờ caller tạo collection một lần từ đầu, sau đó mọi insert đều thuần và song song an toàn.

`types.py` chứa các hình dạng trung lập với backend — `RetrievedChunk`, và cây filter gồm `FieldCondition` / `FilterGroup` với `FilterOperator` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `nin`) và `FilterCombinator` (`and`, `or`). Không gì trong module này được phép import SDK của nhà cung cấp; đây là những hình dạng duy nhất tầng service nhìn thấy, nên việc đổi Qdrant sang Milvus không bao giờ lan lên trên `app.db`.

`VectorStoreFactory` phân giải tên provider thành implementation. Backend được định địa chỉ bằng *đường dẫn module* và import lúc dùng lần đầu chứ không phải ở mức module — điều đó giữ đồ thị import không có chu trình, và nghĩa là một backend chưa cài SDK chỉ fail khi thực sự bị yêu cầu. Startup đẩy connection sống xuống qua `register_connection()`; factory không bao giờ import `app.startup`. `get_store(collection_name, provider)` nhận provider ghi trên row của vector store, nên collection cũ vẫn chạy sau khi `VECTOR_STORE_PROVIDER` đổi; `get_connection` raise `RuntimeError` có nêu cách sửa khi một store tham chiếu tới provider mà deployment này không chạy.

Mỗi backend cung cấp một `filter_translator.py` để render cây trung lập sang ngôn ngữ của chính nó (`to_qdrant_filter`, `to_milvus_expression`).

**Qdrant** (`provider/qdrant/`) — `AsyncQdrantVectorStore` tạo collection với HNSW + quantization cấu hình được (`scalar`/`binary`/`product`), `indexing_threshold=0` lúc tạo rồi nâng lên `20000` sau khi insert hàng loạt để tránh chi phí indexing giữa chừng. `retrieve` chạy một truy vấn cho mỗi query vector, gom song song, và trả về `RetrievedChunk`.

**Milvus** (`provider/milvus/`) — mọi method đều raise `NotImplementedError`. Class này tồn tại để provider đã được nối dây đầy đủ (validate config, `VectorStoreType`, `VectorStoreFactory`, startup), nghĩa là bật Milvus sau này chỉ là điền thân hàm cộng thêm `pymilvus` — không đổi gì phía trên `app.db`.

## Cấu hình (`app/core/config/`)

Hai lớp được gộp theo từng module domain:

1. **pydantic-settings** (`settings.py`) — đọc `.env` / biến môi trường thật. Các field bắt buộc (không có default) gồm API key, credential Postgres/MinIO/Qdrant/Langfuse. Validator bắt buộc `API_VERSION` bắt đầu bằng `v`, port dương, secret không rỗng, `LOG_LEVEL`/`LOG_FORMAT` nằm trong tập cho phép, và mỗi biến `EMBEDDING_PROVIDER` / `CHUNKING_PROVIDER` / `PDF_PARSER_PROVIDER` / `VECTOR_STORE_PROVIDER` phải là một backend đã biết — nên gõ sai sẽ fail lúc startup, không phải lúc dùng lần đầu.
2. **YAML** (`config/config.yaml`, nạp qua `YamlConfigLoader`) — các tunable ổn định: `api.num_workers`, `storage.uploaded_file_bucket`/`max_file_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`.

Các module theo domain (`database.py`, `storage.py`, `models.py`, `embedding.py`, `redis.py`, `langfuse.py`, `api.py`) gộp cả hai nguồn thành hằng số phẳng import được từ `app.core.config`. Danh sách tham số đầy đủ: [Configuration Reference](../CONFIGURATION.md).

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
- `vector_store/types.py` — `VectorStoreType` (`qdrant`, `milvus`) và `SearchType` (hiện chỉ có `dense`). `SearchType` đặt tên cho *toàn bộ* hình dạng retrieval thay vì phơi riêng danh sách retriever và chiến lược fusion, vì hai thứ đó luôn phải khớp nhau — đặt tên cho tổ hợp khiến các trạng thái sai không biểu diễn được.
- `chunking/` — enum `ChunkingStrategy`, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Startup / bootstrap (`app/startup.py`)

Các biến global kiểu service-locator, đặt bởi `init_embed_model`, `init_parsing_service`, `init_postgres`, `init_minio`, `init_vector_store` và đọc qua các getter tương ứng (`get_dense_embedding`, `get_parsing_service`, `get_postgres_pool`, `get_postgres_client`, `get_minio_service`, `get_vector_store_connection`).

`init_embed_model` dựng `EmbeddingService.from_settings()` cho provider đã cấu hình rồi smoke-test nó. `init_parsing_service` dựng các provider một lần để client của backend PDF được tái sử dụng qua nhiều file thay vì dựng lại mỗi lần ingest. `init_vector_store` dựng connection cho `VECTOR_STORE_PROVIDER`, kiểm tra nó, rồi đăng ký với `VectorStoreFactory`. `wait_for_postgres` thử lại pool 5 lần cách nhau 0.5s và raise lại lỗi cuối cùng thay vì chạy tiếp như thể Postgres vẫn truy cập được.

Được dùng giống hệt nhau — nhưng khởi tạo độc lập — bởi cả `app/app.py` (web) và `app/tasks/broker.py` (worker), nên không có biến global riêng cho worker.

## Background worker (`app/tasks/`)

`broker.py` chỉ nắm vòng đời của broker: `RedisStreamBroker` + `RedisAsyncResultBackend` trên `REDIS_URL`, với các hook `WORKER_STARTUP`/`WORKER_SHUTDOWN` chạy `_initialize_services()` (phản chiếu startup event của `app.app`) đúng một lần, rồi đóng pool Postgres cùng `VectorStoreFactory.close_all()` khi thoát. Việc tách task sang module riêng là thứ cho phép `app.tasks.broker:broker` làm entrypoint deploy mà không phải import cả ingestion pipeline chỉ để khởi động tiến trình.

`ingestion_task.py` chứa task duy nhất, `ingest_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id, trace_context, vector_store_type)`. Nó chỉ điều phối: bind `request_id_ctx`, uỷ quyền cho `IngestionService`, log rồi raise lại để TaskIQ thấy được lỗi, cuối cùng reset contextvar.

Xem [Flow](FLOW_vi.md) để có sequence diagram đầy đủ.
