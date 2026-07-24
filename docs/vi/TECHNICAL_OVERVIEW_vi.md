# RetrievalApiPlatform — Retrieval Engine tương thích OpenAI Files & Vector Stores API

Backend hướng production hiện thực mô hình resource **Files** và **Vector Stores** của OpenAI cho các hệ thống RAG, sử dụng:

- FastAPI (`app/app.py`) làm tầng API — `file_router` (`/v1/files`) và `vector_store_router` (`/v1/vector_stores`)
- PostgreSQL (`asyncpg`) cho metadata file/vector store
- MinIO làm kho object storage cho byte file gốc đã upload
- Qdrant làm vector database — một collection riêng cho mỗi vector store
- Redis Streams + TaskIQ cho ingest bất đồng bộ (parse → chunk → embed → upsert) chạy nền, ngoài vòng đời request/response
- Endpoint embedding bên ngoài tương thích OpenAI (vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B`) để tính vector thực sự
- Langfuse (qua OpenTelemetry OTLP) cho tracing end-to-end xuyên suốt upload/ingest/search

---

# Kiến trúc

## Tổng quan

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    CLIENT (openai Python SDK / HTTP)                     │
│      client.files.* / client.vector_stores.* ... / Bearer token          │
└────────────────────────────────┬─────────────────────────────────────────┘
                                  │ HTTP  /v1/...
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Application  (app/app.py)                     │
│  RequestIDMiddleware · verify_api_key (Bearer) · validate_file · routers │
└───┬────────────────────────────────────┬─────────────────────────────────┘
    │                                    │
    ▼                                    ▼
file_router                       vector_store_router
/v1/files                         /v1/vector_stores{,/search}
    │                                    │
    ▼                                    ▼
FileService                       VectorStoreService
    │                                    │
    ▼                                    ▼
PostgresFileStore · MinioFileStore       PostgresVectorStore · AsyncQdrantVectorStore
                                          │
                                          │ create() → status=in_progress, trả response NGAY
                                          ▼
                             process_vector_store_files.kiq(...)
                             (đẩy job ingest vào Redis Stream)
                                          │
                                          ▼
                    ┌─────────────────────────────────────────┐
                    │   TaskIQ Worker (container riêng,        │
                    │   taskiq_worker.py)                      │
                    │   process_vector_store_files             │
                    └────────────────────┬──────────────────────┘
                                          ▼
                  load_and_chunk_file()  ──▶  embed_and_upload_chunks()
                  (MinIO download + parser + Chonkie)   (embedding + Qdrant upsert)
                                          │
                          ┌───────────────┴────────────────┐
                          ▼                                 ▼
              Endpoint embedding bên ngoài          AsyncQdrantVectorStore
              (vLLM, tương thích OpenAI)             .insert_documents()
                          │                                 │
                          └───────────────┬─────────────────┘
                                          ▼
                        PostgresVectorStore.update(status=completed | failed)

      Mọi bước (upload · create · search · ingest) ──▶ traced_span() ──▶ Langfuse (OTel OTLP)
```

## Luồng request tổng quan

**Bước 1 — Auth + validate**

Mọi route (trừ `/health`) phụ thuộc `verify_api_key`, kiểm tra header `Authorization: Bearer <FASTAPI_API_KEY>` tĩnh. Upload file đi qua dependency `validate_file` (kiểm tra MIME/extension khớp nhau + kích thước) trước khi vào handler; path parameter `vector_store_id` được kiểm tra đúng prefix `vs` trước khi truy vấn.

**Bước 2 — Upload file**

`POST /v1/files` — `FileService.upload_file` sinh `file_id` (`file-{8 hex}`), upload byte lên MinIO, sau đó insert row metadata vào Postgres, rồi trả `FileObject` ngay. File upload xong **chưa** được parse/chunk/embed — bước đó chỉ diễn ra khi file được gắn vào một vector store.

**Bước 3 — Tạo vector store → ingest bất đồng bộ**

`POST /v1/vector_stores {name, file_ids, chunking_strategy}` — `VectorStoreService.create` tạo row Postgres với `status=in_progress`, đẩy task `process_vector_store_files.kiq(...)` vào Redis Stream, rồi trả `VectorStoreObject` ngay — không chờ ingest xong. Việc sinh vector diễn ra sau, trên một process worker khác.

**Bước 4 — Ingest nền (TaskIQ worker)**

`process_vector_store_files` chạy trong `taskiq_worker.py`: `check_existing_files` bỏ qua ingest nếu toàn bộ file đã bị xóa; nếu còn, `load_and_chunk_file` tải byte từ MinIO, chọn parser theo extension (`ParserFactory`) rồi chunk bằng Chonkie; `embed_and_upload_chunks` embed từng batch (`EMBEDDING_UPLOAD_BATCH_SIZE=16`) và upsert vào Qdrant — batch đầu chạy riêng lẻ để tạo collection trước, tránh race condition, các batch còn lại chạy song song giới hạn bởi `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)`. Lỗi ở bất kỳ bước nào chuyển `status=failed`; thành công thì `status=completed`.

**Bước 5 — Search**

`POST /v1/vector_stores/{id}/search {query, max_num_results}` — `VectorStoreService.search` embed query rồi gọi thẳng `AsyncQdrantVectorStore.retrieve` (không đi qua ingest pipeline). Nếu collection Qdrant chưa tồn tại (vd. đang ingest hoặc ingest lỗi trước khi tạo collection), trả `data=[]` thay vì lỗi.

> Để biết chính xác tên hàm, tên biến, và logic từng gate: [FLOW_vi.md](FLOW_vi.md)

---

# HTTP Endpoints

Mọi route có tiền tố `/v1` và yêu cầu `Authorization: Bearer <FASTAPI_API_KEY>`.

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/files` | Upload file (multipart: `purpose`, `file`, `expires_after`) |
| `GET` | `/files` | Liệt kê file (phân trang, lọc theo `purpose`) |
| `GET` | `/files/{file_id}` | Lấy thông tin file |
| `DELETE` | `/files/{file_id}` | Xóa file |
| `POST` | `/vector_stores` | Tạo vector store (kèm `file_ids` → kích hoạt ingest nền) |
| `GET` | `/vector_stores` | Liệt kê vector store (phân trang theo cursor) |
| `GET` | `/vector_stores/{vector_store_id}` | Lấy thông tin vector store, bao gồm `status`/`file_counts` |
| `POST` | `/vector_stores/{vector_store_id}` | Sửa vector store (kiểu OpenAI: dùng `POST`, không phải `PATCH`) |
| `DELETE` | `/vector_stores/{vector_store_id}` | Xóa vector store |
| `POST` | `/vector_stores/{vector_store_id}/search` | Tìm kiếm theo `query`, `max_num_results` (`filters`/`ranking_options` được chấp nhận nhưng chưa áp dụng) |

Cả hai router dùng model đối tượng theo chuẩn OpenAI (`FileObject`, `VectorStoreObject`, response phân trang `object="list"`), ID theo đúng convention của OpenAI (`file-{8 hex}`, `vs-{32 hex}` — `app/utils/key_generator/key_generator.py`), và error envelope kiểu OpenAI (`{message, type, params, code}`) cho mọi `AppBaseException`.

Chưa có: ingest nhiều file cho một vector store, endpoint sub-resource cho vector store file (attach/list/detach), parser cho `.docx`, hủy/theo dõi tiến trình ingest ở mức chunk. Xem [README_vi.md](README_vi.md#to-do--roadmap).

---

# Background Worker

| | |
|---|---|
| Hàng đợi | Redis Streams (`RedisStreamBroker`, TaskIQ) |
| Task | `process_vector_store_files` (`taskiq_worker.py`) |
| Container | `taskiq_worker`, `restart: always`; phụ thuộc `postgres`, `redis`, `minio` healthy (`compose_web.yml`) |
| Giới hạn xử lý | Chỉ hỗ trợ đúng **một** file mỗi vector store hiện tại — file dư/nhiều file bị bỏ qua âm thầm (multi-file ingest là TODO) |
| Batch embedding | `EMBEDDING_UPLOAD_BATCH_SIZE=16`; song song giới hạn bởi `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)`; batch đầu chạy riêng để tránh race khi tạo Qdrant collection |
| Idempotency / lỗi | `check_existing_files` bỏ qua load/chunk/embed nếu file đã bị xóa hết; lỗi ở bất kỳ bước nào → `_mark_failed` (`status=failed`) → re-raise |
| Correlation | `request_id_ctx` được set lại trong worker từ `request_id` truyền qua `.kiq(...)`, để log worker correlate được với request HTTP gốc |

---

# Cấu trúc repository

```
app/
  app.py                  # FastAPI app: middleware, router, exception handler, startup event
  router/                 # file_router.py, vector_store_router.py
  schemas/                # base/, file/, vector_store/, chunking/ — Pydantic request/response model
  services/
    file/                 # FileService — upload/list/get/delete (MinIO + Postgres)
    vector_store/         # VectorStoreService — create/list/get/modify/delete/search
    ingest/                # file_loader.py (parse+chunk), ingest_pipeline.py (embed+upsert)
    parsers/               # BaseTextParser, AsyncTextParser, LlamaParseParser, ParserFactory
    chunking/              # ChonkieChunkingService (đang dùng), LangchainChunkingService (không dùng trong ingest)
  db/
    minio/                 # MinioService, MinioFileStore — object storage
    postgres/              # PostgresClient, PostgresFileStore, PostgresVectorStore, schema/
    qdrant/                # QdrantService, AsyncQdrantVectorStore
  core/
    config/                # settings.py (.env) + các module theo domain, merge YAML + env
    tracing/               # init_tracing(), traced_span() — Langfuse qua OTel OTLP
    request_context.py     # request_id_ctx ContextVar
  security/auth.py         # verify_api_key (Bearer, một API key tĩnh)
  middleware/request_id.py # RequestIDMiddleware (ASGI thuần, không dùng BaseHTTPMiddleware)
  exceptions/              # Hệ thống AppBaseException + common_exception_handler
  dependencies/file_validation.py  # validate_file (kiểm tra kích thước + MIME/extension)
  startup/startup.py       # init_embed_model/init_postgres/init_minio/init_qdrant + getter
  utils/                   # config_loader, datetime_utils, io, key_generator, helper tracing
taskiq_worker.py           # RedisStreamBroker + task process_vector_store_files
config/config.yaml         # tham số version-controlled (batch size, tên bucket, ...)
docker/                    # Dockerfile + compose_db.yml / compose_web.yml / compose_tracking.yml
examples/file_upload_example.py  # demo end-to-end dùng SDK openai
```

---

# Docker Compose topology

Ba file compose được gộp lại qua `Makefile`:

- **`compose_db.yml`** — `postgres` (5432), `redis` (6379), `qdrant` (6333 HTTP / 6334 gRPC)
- **`compose_tracking.yml`** — `minio` (9000 API / 9001 console) — object storage, dù tên file là "tracking"
- **`compose_web.yml`** — `web` (uvicorn, 8005; phụ thuộc `postgres` healthy + `worker` started) và `worker` (TaskIQ; phụ thuộc `postgres`, `redis`, `minio` healthy)

Langfuse **không** nằm trong Compose stack này — cấu hình trỏ tới một instance self-hosted/external. Tương tự với embedding server — `VLLM_DENSE_EMBEDDING_URL` mặc định là `http://172.17.0.1:8100/v1` (máy host, không phải một service trong Compose).

Cả `web` và `worker` chạy từ cùng một image (`docker/Dockerfile`, build multi-stage với `uv sync --frozen`, chạy bằng user không phải root `appuser`), chỉ khác entrypoint command (`uvicorn app.app:app` so với `taskiq worker taskiq_worker:broker`) — mỗi process tự chạy độc lập cùng một quy trình bootstrap `startup`/`initialize_services`.

---

# Cấu hình

Xem [CONFIGURATION.md](../CONFIGURATION.md) để có bảng tham số đầy đủ: `.env` (hạ tầng/secret) so với `config/config.yaml` (tham số tĩnh) so với giá trị mặc định trong code.

---

> Chi tiết từng component và API reference: [DETAILED_COMPONENTS_vi.md](DETAILED_COMPONENTS_vi.md)
