# Detailed Components

## Router layer (`app/router/`)

### `file_router.py` — tag "File"

Mọi endpoint đều yêu cầu `Depends(verify_api_key)`.

| Method | Path | Handler | Ghi chú |
|---|---|---|---|
| POST | `/v1/files` | `upload_file` | Form field `purpose`, `file` (qua `Depends(validate_file)`), `expires_after[anchor|seconds]` |
| GET | `/v1/files` | `list_files` | Query qua `FileQueryRequest` (kế thừa `PaginationParams`) |
| GET | `/v1/files/{file_id}` | `get_file_by_id` | — |
| DELETE | `/v1/files/{file_id}` | `delete_file` | Trả về `FileDeletedResponse` |

### `vector_store_router.py` — tag "Vector Stores"

| Method | Path | Handler | Ghi chú |
|---|---|---|---|
| POST | `/v1/vector_stores` | `create_vector_store` | Body `VectorStoreCreateRequest`; enqueue job ingest |
| GET | `/v1/vector_stores` | `list_vector_stores` | Query `VectorStoreQueryRequest` |
| GET | `/v1/vector_stores/{id}` | `get_vector_store` | — |
| POST | `/v1/vector_stores/{id}` | `modify_vector_store` | Kiểu OpenAI: update dùng POST, không phải PATCH |
| DELETE | `/v1/vector_stores/{id}` | `delete_vector_store` | — |
| POST | `/v1/vector_stores/{id}/search` | `search_vector_store` | Body `VectorStoreSearchRequest`; `filters`/`ranking_options` được chấp nhận nhưng chưa áp dụng |

`app/app.py` còn định nghĩa `GET /health` (loại khỏi OpenAPI schema), đăng ký `RequestIDMiddleware`, exception handler toàn cục cho `AppBaseException`, và một startup event khởi tạo lần lượt: tracing → embed model → Postgres (pool + `wait_for_postgres` + tạo bảng) → Qdrant → MinIO.

## Service layer (`app/services/`)

### `FileService` (`file/file_service.py`)

Các static method `upload_file`, `list_files`, `get_file_by_id`, `delete_file`. Luồng upload: sinh `file_id`, tạo object path `{api_key}/uploads/{uuid}_{filename}`, upload byte lên MinIO, sau đó insert row metadata vào Postgres. Nếu insert Postgres thất bại sau khi đã upload MinIO thành công, object bị bỏ lại orphan (log warning, không dọn dẹp) và `PostgresConnectionException` được raise.

### `VectorStoreService` (`vector_store/vector_store_service.py`)

Các static method `create`, `list`, `get`, `modify`, `delete`, `search`. Kết hợp `PostgresVectorStore` (metadata/status), `AsyncQdrantVectorStore` (vector), TaskIQ (`process_vector_store_files.kiq(...)` enqueue từ `create`), và `traced_span` cho tracing. `search()` embed trực tiếp query text rồi gọi `AsyncQdrantVectorStore.retrieve` — **không** đi qua ingest pipeline; pipeline đó chỉ dành cho TaskIQ worker.

### Ingest pipeline (`services/ingest/`)

- `file_loader.py::load_and_chunk_file(minio_client, file_metadata, chunk_size)` — chọn parser qua `ParserFactory.get(file_ext)`, download byte từ MinIO, parse ra text, chunk qua `ChonkieChunkingService`.
- `ingest_pipeline.py::embed_and_upload_chunks` (và `embed_and_insert_batch[_bounded]`) — embed các batch chunk và upsert vào `AsyncQdrantVectorStore.insert_documents`. Batch đầu tiên chạy riêng lẻ (tránh race condition khi tạo collection), các batch còn lại chạy đồng thời, giới hạn bởi `EMBEDDING_BATCH_CONCURRENCY`.

Sơ đồ kết hợp: **Router → Service → (DB client / enqueue TaskIQ) → Worker task → file_loader (parser + chunker) → ingest_pipeline (embed + upsert) → Qdrant**.

### Parsers (`services/parsers/`)

`BaseTextParser(ABC)` định nghĩa một method abstract duy nhất: `async parse(file_bytes: bytes) -> str` — interface đã được thống nhất quanh nguyên tắc nhận vào raw bytes, trả ra text (parser UndatasIO cũ đã bị gỡ bỏ hoàn toàn).

Registry của `ParserFactory`, khóa theo extension viết thường, instance được cache theo từng extension:

| Extension | Parser | Ghi chú |
|---|---|---|
| `.txt`, `.md` | `AsyncTextParser` | Decode byte (`errors="ignore"`) trong thread pool |
| `.pdf` | `LlamaParseParser` | Wrap `llama_parse.LlamaParse` (output Markdown); ghi ra file tạm vì LlamaParse cần một đường dẫn |

Lưu ý sự chênh lệch: `ALLOWED_EXTENSIONS` lúc upload còn cho phép `.docx`, `.csv`, `.json`, và ảnh — các loại này **upload được** nhưng chưa có parser đăng ký, nên khi ingest sẽ raise `ValueError("Unsupported file format: ...")`.

### Chunking (`services/chunking/`)

`ChonkieChunkingService` (đang được ingest pipeline sử dụng) — config `ChonkieChunkingConfig`, strategy enum `character | sentence | recursive | token` (mặc định `recursive`, `chunk_size=800`, `chunk_overlap=400`). Map sang `chonkie.TokenChunker` / `SentenceChunker` / `RecursiveChunker` tùy theo strategy.

`LangchainChunkingService` cũng tồn tại (wrap `langchain_text_splitters`) nhưng chưa được nối vào ingest path hiện tại — coi như implementation cũ/thay thế.

Ở tầng API, `VectorStoreCreateRequest.chunking_strategy` chỉ expose `"auto"` hoặc `"static"` (với `max_chunk_size_tokens`/`chunk_overlap_tokens`) — chưa có cách chọn `sentence`/`token`/`character` qua API công khai dù chunker bên dưới hỗ trợ. Ngoài ra, `chunk_overlap` được tính trong `VectorStoreService.create` nhưng không được truyền thực sự vào task enqueue (chỉ `chunk_size` được truyền) — một gap đã biết, xem [Design Decisions](DESIGN_DECISIONS_vi.md).

## Data layer (`app/db/`)

| Package | Class | Backing cho | Docker service |
|---|---|---|---|
| `minio/` | `MinioService`, `MinioFileStore` | Byte của file đã upload (bucket `uploaded-files`) | `minio` (9000/9001) |
| `postgres/` | `PostgresClient`, `PostgresFileStore`, `PostgresVectorStore` | Bảng metadata `files` + `vector_stores` (ownership theo `api_key`, status, metadata JSONB) | `postgres` (5432) |
| `qdrant/` | `QdrantService`, `AsyncQdrantVectorStore` | Một collection Qdrant cho mỗi vector store (`collection_name == vector_store_id`) | `qdrant` (6333/6334) |

`AsyncQdrantVectorStore` tạo collection lazy ở lần insert đầu tiên (HNSW + quantization có thể cấu hình — `scalar`/`binary`/`product` — với `indexing_threshold=0` lúc tạo, nâng lên `20000` sau khi insert hàng loạt để tránh chi phí indexing giữa chừng). `retrieve` chạy một lệnh `query_points` cho mỗi query vector, gather song song.

## Configuration (`app/core/config/`)

Hai lớp được merge theo từng module domain:

1. **pydantic-settings** (`settings.py`) — đọc `.env` / biến môi trường thật. Các field bắt buộc (không có default) gồm API key, credential Postgres/MinIO/Qdrant/Langfuse. Validator đảm bảo `API_VERSION` bắt đầu bằng `v`, port dương, secret không rỗng, `LOG_LEVEL`/`LOG_FORMAT` nằm trong tập hợp lệ.
2. **YAML** (`config/config.yaml`, load qua `YamlConfigLoader`) — tham số ổn định, version-controlled: `api.num_workers`, `storage.uploaded_file_bucket`/`max_file_size`, `redis.url`, `models.dense_model_name`, `embedding.upload_batch_size`/`batch_concurrency`.

Các module theo domain (`database.py`, `storage.py`, `models.py`, `embedding.py`, `redis.py`, `langfuse.py`, `api.py`) gộp cả hai nguồn thành constant phẳng, import được từ `app.core.config`. Danh sách tham số đầy đủ: [Configuration Reference](../CONFIGURATION.md).

## Tracing (`app/core/tracing/`)

Langfuse qua **OpenTelemetry OTLP** exporter — `init_tracing()` dựng `TracerProvider` + `BatchSpanProcessor(OTLPSpanExporter(...))` trỏ tới `{LANGFUSE_BASE_URL}/api/public/otel/v1/traces` với Basic-Auth từ cặp public/secret key. `traced_span(name, attributes)` là một context manager dùng xuyên suốt search và ingest; tự động set `Status.OK`/`Status.ERROR` và record exception.

- **Search**: span ngoài cùng gắn tag `vector_store_search`, nested span `embedding` và `retrieve` (span `retrieve` cố tình loại trừ payload/content của chunk khỏi tracing, chỉ giữ `chunk_id`/`score`).
- **Ingest**: span ngoài cùng gắn tag `vector_store_ingest`, nested span `embedding` và `upsert`.

Cả web app (`app.py`) và TaskIQ worker (`taskiq_worker.py`) đều gọi `init_tracing()` độc lập lúc startup của riêng mình.

## Request correlation (`app/middleware/request_id.py`)

`RequestIDMiddleware` là một ASGI middleware thuần (không dùng `BaseHTTPMiddleware` — vì nó sẽ reset contextvar trước khi access logger của uvicorn kịp ghi log). Middleware này tái sử dụng header `X-Request-Id` do client gửi nếu có, nếu không thì sinh `req_{uuid4().hex}`, bind vào `request_id_ctx` (một `contextvars.ContextVar` trong `app/core/request_context.py`) trong suốt vòng đời request, và echo lại giá trị đó trên response. Cùng một ID được truyền vào `process_vector_store_files.kiq(...)` và bind lại bên trong worker task, nhờ vậy một request ID xuyên suốt cả log HTTP lẫn job ingest bất đồng bộ mà request đó khởi tạo.

## Security (`app/security/auth.py`)

`verify_api_key` yêu cầu header `Authorization: Bearer <token>`, so sánh token với `FASTAPI_API_KEY` được cấu hình duy nhất, và trả về token đó như `api_key` — giá trị này sau đó dùng để scope các row trong Postgres. Vì chỉ có đúng một token hợp lệ, đây thực chất là auth single-tenant dù schema đã được thiết kế cho ownership multi-tenant (xem [Design Decisions](DESIGN_DECISIONS_vi.md)).

## Exceptions (`app/exceptions/`)

Gốc `AppBaseException(status_code, response: BaseResponse, log_message)`. Các subclass theo domain: `auth/` (`APIKeyIncorrectException`, `BearerMissingException` — 401), `file/` (`FileSizeLimitExceededException` 413, `FileNotFoundException` 404), `postgres/` (`PostgresConnectionException` 503), `vector_store/` (`VectorStoreNotFoundException` 404, `WrongPrefixVectorstoreException` 400). `common_exception_handler` (đăng ký toàn cục) log ở mức ERROR/WARNING tùy status, trả về `JSONResponse({message, type, params, code})` — error envelope kiểu OpenAI. Các `HTTPException` thuần (ví dụ 415 từ `dependencies/file_validation.py`) rơi vào handler mặc định của FastAPI.

## Schemas (`app/schemas/`)

- `base/` — `BaseModel` dùng chung (`extra="forbid"`), `PaginationParams`, generic `PaginatedResponse[T]`.
- `file/` — `FileObject`, `FileListObject`, các biến thể request/response, enum `UploadingStatus`.
- `vector_store/` — `VectorStoreCreateRequest`/`ModifyRequest`/`QueryRequest`, các union type cho chunking strategy, type filter/ranking (đã định nghĩa, chưa áp dụng), `VectorStoreObject`, `VectorStoreSearchResponse`.
- `chunking/` — enum `ChunkingStrategy`, `ChonkieChunkingConfig`, `LangchainChunkingConfig`.

## Dependencies (`app/dependencies/file_validation.py`)

`validate_file_size` (từ chối nếu vượt `MAX_FILE_SIZE` MB), `validate_file_type` (kiểm tra MIME + extension + tính nhất quán giữa chúng qua `MIME_TYPE_MAPPING`), kết hợp thành `validate_file` — dùng làm `Depends(validate_file)` ở endpoint upload.

## Startup / bootstrap (`app/startup/startup.py`)

Các biến global kiểu service-locator thủ công (`embed_model`, `postgres_client`, `minio_service`, `qdrant_service`) được set bởi `init_embed_model`/`init_postgres`/`init_minio`/`init_qdrant` và đọc qua các getter tương ứng. `init_embed_model` tạo một client `AsyncOpenAI` trỏ tới `VLLM_DENSE_EMBEDDING_URL` (embedding được phục vụ qua một endpoint tương thích OpenAI chạy vLLM, không phải model local) và smoke-test client đó. Được dùng giống hệt nhau — nhưng khởi tạo độc lập — bởi cả `app/app.py` (web) và `taskiq_worker.py` (worker).

## Background worker (`taskiq_worker.py`)

`RedisStreamBroker` + `RedisAsyncResultBackend`, cả hai đều qua `REDIS_URL`. Hook `WORKER_STARTUP`/`WORKER_SHUTDOWN` khởi tạo/tắt lazy các service giống hệt web app. Task duy nhất, `process_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id)`:

1. Lọc `file_ids` theo Postgres (`check_existing_files`), lấy tổng bytes + metadata
2. **Chỉ xử lý đúng một file** — nếu `file_ids` có nhiều hơn một file, các file dư bị âm thầm bỏ qua (chunked_texts để trống) và vector store vẫn được đánh dấu `completed`
3. Load + chunk (một) file, embed + upsert vào Qdrant
4. Đánh dấu vector store `failed` nếu có lỗi bất kỳ, ngược lại `completed` với `usage_bytes`

Xem [Flow](FLOW_vi.md) cho sequence diagram đầy đủ.
