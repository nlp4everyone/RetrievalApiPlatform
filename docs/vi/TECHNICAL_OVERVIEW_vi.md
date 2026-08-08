# RetrievalApiPlatform — Retrieval Engine tương thích OpenAI Files & Vector Stores API

Backend hướng production, hiện thực mô hình tài nguyên **Files** và **Vector Stores** của OpenAI cho các hệ thống RAG, sử dụng:

- FastAPI (`app/app.py`) làm API layer — `file_router` (`/v1/files`) và `vector_store_router` (`/v1/vector_stores`), với toàn bộ ranh giới HTTP nằm trong `app/api/`
- PostgreSQL (`asyncpg`) cho metadata của file/vector store
- MinIO làm object storage lưu byte gốc của file đã upload
- Một vector database có thể thay thế, nằm sau `BaseAsyncVectorStore` — mỗi vector store là một collection. Qdrant đã triển khai; Milvus đã nối dây nhưng còn là stub
- Redis Streams + TaskIQ cho ingestion bất đồng bộ, chạy ngoài vòng đời request/response
- Hai service parsing bên ngoài: LlamaParse cho `.pdf`, Unstructured API cho mọi định dạng còn lại — cả hai đều trả Markdown
- Một endpoint dense embedding bên ngoài (vLLM tương thích OpenAI, hoặc Text Embeddings Inference) để tính vector — [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) là repo đồng hành đảm nhiệm phần này
- Langfuse (qua OpenTelemetry OTLP) cho tracing end-to-end xuyên suốt upload/ingestion/search

---

# Kiến trúc

## Các layer

Đồ thị import chạy nghiêm ngặt từ trên xuống — `app.components` và `app.pipelines` không bao giờ với ngược lên `app.api`, và đó chính là lý do TaskIQ worker chạy được mà không cần FastAPI trong đường import của nó.

```
app/api          ranh giới HTTP       router, validation qua Depends, auth, middleware
app/services     business logic       FileService, VectorStoreService, IngestionService
app/pipelines    điều phối            Pipeline + BaseStage; ingestion & retrieval
app/components   năng lực             parsing, chunking, embedding (theo provider)
app/db           lưu trữ              postgres, minio, vector_store (theo provider)
app/core         xuyên suốt           config, tracing, request context
```

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
│                       — tất cả đến từ app/api/ —                         │
└───┬────────────────────────────────────┬─────────────────────────────────┘
    │                                    │
    ▼                                    ▼
file_router                       vector_store_router
/v1/files                         /v1/vector_stores{,/search}
    │                                    │
    ▼                                    ▼
FileService                       VectorStoreService
    │                                    │
    ▼                         ┌──────────┴───────────────┐
PostgresFileStore             │                          │
MinioFileStore                ▼                          ▼
                        create()                     search()
              status=in_progress, trả về           RetrievalPipeline
                      NGAY LẬP TỨC                  (in-process)
                          │                          │
                          ▼                    embed_query → retrieve → fuse
        ingest_vector_store_files.kiq(...)           │
        + inject_trace_context()  ──────────▶  DenseRetriever
        (đẩy job lên Redis Stream)                   │
                          │                          ▼
                          ▼                  VectorStoreFactory.get_store()
       ┌──────────────────────────────────┐          │
       │  TaskIQ Worker (container riêng) │          ▼
       │  app.tasks.broker:broker         │   BaseAsyncVectorStore
       │  app.tasks.ingestion_task        │   (Qdrant | Milvus*)
       └────────────────┬─────────────────┘
                        ▼
              IngestionService.ingest_vector_store_files()
                        │
                        ▼
              IngestionPipeline (app/pipelines/ingestion)

      download  ──▶  parse    ──▶  chunk    ──▶  embed_index
      MinIO          Parsing       Chunking      EmbedAndIndexStage
      semaphore      Service       Service       mỗi batch (16 chunk):
      + pool I/O     LlamaParse    pool CPU        embed → Document → upsert
                     (.pdf)                        trong cùng 1 semaphore(4)
                     Unstructured                ensure_collection() đúng 1 lần,
                     (còn lại)                   embedding_dim từ cache startup
                        │
                        ▼
        PostgresVectorStore.update(status=completed | failed)

  Pipeline.run() mở một span cho mỗi stage ──▶ Langfuse (OTel OTLP)
  trace_context đi kèm task, nên span của worker nhập vào trace của request

  * Milvus đã đăng ký đầy đủ nhưng mọi method đều raise NotImplementedError
```

## Luồng xử lý tổng thể

**Bước 1 — Auth + validate**

Mọi route (trừ `/health`) đều phụ thuộc `verify_api_key` (`app/api/security.py`), kiểm tra header `Authorization: Bearer <FASTAPI_API_KEY>` với một key tĩnh. Upload file đi qua dependency `validate_file` (`app/api/dependencies.py` — khớp MIME/extension + kiểm tra kích thước) trước khi tới handler; path parameter `vector_store_id` được kiểm tra prefix `vs` trước mọi truy vấn.

**Bước 2 — Upload file**

`POST /v1/files` — `FileService.upload_file` sinh `file_id` (`file-{8 hex}`), upload byte lên MinIO, rồi insert một row metadata vào Postgres, và trả `FileObject` về ngay. File **không** được parse/chunk/embed tại thời điểm upload — việc đó chỉ xảy ra khi file được gắn vào một vector store.

**Bước 3 — Tạo vector store → ingestion bất đồng bộ**

`POST /v1/vector_stores {name, file_ids, chunking_strategy}` — `VectorStoreService.create` từ chối ngay nếu có nhiều hơn một `file_id` (`UnsupportedMultipleFilesException`, 400), tạo row Postgres với `status=in_progress` kèm provider mà nó được tạo trên đó, đẩy `ingest_vector_store_files.kiq(...)` lên Redis Stream cùng W3C trace context hiện tại, và trả `VectorStoreObject` về ngay — không chờ ingestion xong.

**Bước 4 — Ingestion nền (TaskIQ worker)**

`ingest_vector_store_files` (`app/tasks/ingestion_task.py`) chỉ là một adapter mỏng: bind correlation id rồi uỷ quyền cho `IngestionService.ingest_vector_store_files`. Service đó xác định file nào còn tồn tại, dựng `IngestionPipeline` qua `build_ingestion_pipeline(...)`, và chạy nó trên một `IngestionContext`. Bốn stage — `download`, `parse`, `chunk`, `embed_index` — đều đọc và ghi vào chính context object đó. Bất kỳ lỗi nào cũng chuyển `status=failed` và raise lại; thành công thì đặt `status=completed` kèm `usage_bytes`.

Bước cuối là **một** stage streaming (`EmbedAndIndexStage`) chứ không phải hai: mỗi batch chunk được embed rồi upsert ngay trong cùng một lượt giữ semaphore, nên việc ghi bắt đầu từ batch đầu tiên thay vì chờ embed xong cả file, và bộ nhớ đỉnh bị chặn ở `batch_size × concurrency` chứ không theo kích thước file. Collection được tạo trước vòng lặp từ số chiều vector đã cache lúc startup (`get_dense_embedding_dim()`) — đó chính là thứ khiến streaming khả thi, vì không còn phải chờ kết quả embed đầu tiên để biết `embedding_dim`.

Tiến trình worker chạy trên **hai** thread pool tách biệt cộng một trần download: pool I/O (`IO_THREAD_POOL_SIZE`) cho transfer MinIO, pool CPU (`CPU_THREAD_POOL_SIZE`) cho chunking, và `asyncio.Semaphore(DOWNLOAD_CONCURRENCY)` giới hạn số file tải song song. Dùng chung một pool sẽ khiến chunking (CPU-bound) xếp hàng sau các lượt transfer chậm, và một loạt job ingestion đồng thời có thể tự làm cạn pool.

**Bước 5 — Search**

`POST /v1/vector_stores/{id}/search {query, max_num_results, filters}` — `VectorStoreService.search` đọc row của vector store (để biết `vector_store_type`), chuẩn hoá `filters` thành cây trung lập theo backend, rồi chạy `RetrievalPipeline` ngay trong tiến trình: `embed_query → retrieve → fuse`. `DenseRetriever` trả `[]` thay vì báo lỗi khi collection chưa tồn tại (ingestion còn đang chạy, hoặc đã thất bại trước khi collection được tạo).

> Để biết chính xác tên hàm, tên biến và logic từng nhánh: [FLOW_vi.md](FLOW_vi.md)

---

# Pipelines

Cả hai pipeline dùng chung một bộ máy (`app/pipelines/pipeline.py`), chỉ khác tập stage. `Pipeline.run()` là nơi **duy nhất** mở span: stage chỉ chứa business logic, khai báo `name`, và báo cáo metric qua `span_attributes()`. Một stage có thể trả `False` từ `emits_span()` để không xuất hiện trong trace ở lần chạy mà nó không làm gì — nhưng nó vẫn được thực thi.

| | Ingestion | Retrieval |
|---|---|---|
| Chạy ở | TaskIQ worker | tiến trình web, trong request |
| Context | `IngestionContext` | `RetrievalContext` |
| Các stage | `download → parse → chunk → embed_index` | `embed_query → retrieve → fuse` |
| Dựng bởi | `build_ingestion_pipeline(...)` | `build_retrieval_pipeline(..., search_type)` |
| Trace cha | `trace_context` đi kèm task | span của request hiện tại |

Thêm một bước là thêm một subclass `BaseStage` và một dòng trong factory. Hybrid search đúng y như vậy: một `HybridRetriever` cộng một nhánh trong `_build_plan()`, stage và pipeline giữ nguyên — và không cần chiến lược fusion mới, vì Qdrant trộn nhánh dense với sparse bằng RRF ngay trong chính câu truy vấn.

---

# HTTP Endpoints

Mọi route đều có prefix `/v1` và yêu cầu `Authorization: Bearer <FASTAPI_API_KEY>`.

| Method | Path | Mô tả |
|---|---|---|
| `POST` | `/files` | Upload file (multipart: `purpose`, `file`, `expires_after`) |
| `GET` | `/files` | Liệt kê file (phân trang, lọc theo `purpose`) |
| `GET` | `/files/{file_id}` | Lấy thông tin một file |
| `DELETE` | `/files/{file_id}` | Xoá file |
| `POST` | `/vector_stores` | Tạo vector store (một `file_id` → kích hoạt ingestion nền; nhiều hơn → 400) |
| `GET` | `/vector_stores` | Liệt kê vector store (phân trang bằng cursor) |
| `GET` | `/vector_stores/{vector_store_id}` | Lấy thông tin vector store, gồm `status`/`file_counts` |
| `POST` | `/vector_stores/{vector_store_id}` | Sửa vector store (kiểu OpenAI: dùng `POST`, không phải `PATCH`) |
| `DELETE` | `/vector_stores/{vector_store_id}` | Xoá vector store |
| `POST` | `/vector_stores/{vector_store_id}/search` | Search theo `query`, `max_num_results`, `filters`, `search_type` (`auto`\|`dense`\|`hybrid`, phần mở rộng ngoài OpenAI); trong `ranking_options` mới chỉ `score_threshold` được áp dụng |

Cả hai router đều dùng object model của OpenAI (`FileObject`, `VectorStoreObject`, response phân trang `object="list"`), quy ước ID của OpenAI (`file-{8 hex}`, `vs-{32 hex}` — `app/utils/key_generator/key_generator.py`), và error envelope kiểu OpenAI (`{message, type, params, code}`) cho mọi `AppBaseException`.

Chưa triển khai: ingestion nhiều file cho một vector store, endpoint sub-resource vector-store-file (attach/list/detach), parser cho `.csv`/`.json`/`.gif` (upload được nhưng không có provider), tiến độ/huỷ ingestion ở mức chunk. Xem [README_vi.md](README_vi.md#to-do--roadmap).

---

# Background Worker

| | |
|---|---|
| Queue | Redis Streams (`RedisStreamBroker`, TaskIQ) |
| Entrypoint | `taskiq worker app.tasks.broker:broker` |
| Task | `ingest_vector_store_files` (`app/tasks/ingestion_task.py`) |
| Business logic | `IngestionService` (`app/services/ingestion/`) — không import TaskIQ, nên gọi và test được mà không cần broker |
| Container | `taskiq_worker`, `restart: always`; phụ thuộc `postgres`, `redis`, `minio` khoẻ mạnh (`compose_web.yml`) |
| Giới hạn xử lý | Đúng **một** file cho mỗi vector store. `VectorStoreService.create` từ chối ngay tại request; `IngestionService` kiểm tra lại và đánh dấu store `failed` thay vì báo `completed` trên một store rỗng |
| Chia batch | `EMBEDDING_UPLOAD_BATCH_SIZE=16` chunk mỗi batch, dùng cho cả lời gọi embed lẫn lời gọi upsert của batch đó; toàn bộ vòng lặp bị giới hạn bởi **một** `asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)` |
| Streaming | `EmbedAndIndexStage` embed rồi upsert từng batch trong cùng một lượt giữ semaphore. `Document` chỉ được dựng **sau** khi lấy được semaphore, nên tối đa `concurrency` batch chunk/vector/Document tồn tại cùng lúc thay vì cả file |
| Tạo collection | `ensure_collection(embedding_dim)` gọi đúng một lần trước vòng lặp, với `embedding_dim` lấy từ `get_dense_embedding_dim()` (cache lúc startup bởi `EmbeddingService.check_connection()`), nên mọi insert đều là ghi thuần và chạy song song được |
| Thread pool | Pool I/O (`IO_THREAD_POOL_SIZE=32`) cho MinIO tách khỏi pool CPU (`CPU_THREAD_POOL_SIZE=4`) cho chunking; `MinioFileStore._fetch_object` gộp `get_object()` + `.read()` thành một lời gọi duy nhất trên pool I/O, vì mở stream chỉ tốn header còn transfer thật nằm ở `.read()` |
| Trần download | `asyncio.Semaphore(DOWNLOAD_CONCURRENCY=4)` trên mỗi tiến trình worker, để một loạt job ingestion không tự làm cạn pool I/O |
| Correlation | `request_id_ctx` được bind lại trong worker từ `request_id` truyền qua `.kiq(...)`; `trace_context` (W3C) được extract để span ingestion lồng vào trace Langfuse của request gốc |

`app/tasks/broker.py` chỉ quản lý vòng đời của broker — kết nối, bootstrap service khi `WORKER_STARTUP`, đóng khi `WORKER_SHUTDOWN`. Việc tách task sang module riêng là thứ cho phép broker làm entrypoint deploy mà không phải import cả ingestion pipeline chỉ để khởi động tiến trình.

---

# Cấu trúc repository

```
app/
  app.py                  # FastAPI app: middleware, router, exception handler, startup event
  startup.py              # service locator init_*/get_*, dùng chung cho web và worker
                          # (kể cả pool I/O, pool CPU và semaphore download)
  api/                    # mọi thứ tồn tại chỉ vì app được phục vụ qua HTTP
    router/               # file_router.py, vector_store_router.py
    dependencies.py       # validate_file (kích thước + MIME/extension)
    security.py           # verify_api_key (Bearer, một key tĩnh duy nhất)
    middleware.py         # RequestIDMiddleware (ASGI thuần, không phải BaseHTTPMiddleware)
  services/
    file/                 # FileService — upload/list/get/delete (MinIO + Postgres)
    vector_store/         # VectorStoreService — create/list/get/modify/delete/search
    ingestion/            # IngestionService — business logic của task nền
  pipelines/
    base.py, pipeline.py  # contract BaseStage + runner nắm toàn bộ tracing
    ingestion/            # context, factory, pipeline, stages/ (download→parse→chunk→embed_index)
    retrieval/            # context, factory, pipeline, fusion, retriever/, stages/
  components/             # năng lực có thể thay thế: base.py + provider/ + <X>Service.from_settings()
    parsing/              # LlamaParseProvider (.pdf), UnstructuredProvider (mọi định dạng khác)
    chunking/             # ChonkieProvider, LangchainProvider
    embedding/            # OpenAIEmbeddingProvider, TEIEmbeddingProvider
  db/
    minio/                # MinioService, MinioFileStore — object storage
    postgres/             # PostgresClient, PostgresFileStore, PostgresVectorStore, schema/
    vector_store/         # base.py, types.py, factory.py + provider/qdrant, provider/milvus
  core/
    config/               # settings.py (.env) + các module theo domain gộp YAML + env
    tracing/              # init_tracing(), traced_span(), truyền trace context, attributes
    request_context.py    # ContextVar request_id_ctx
  schemas/                # base/, file/, vector_store/, chunking/ — model Pydantic request/response
  exceptions/             # cây AppBaseException + common_exception_handler
  tasks/
    broker.py             # RedisStreamBroker + startup/shutdown của worker (entrypoint deploy)
    ingestion_task.py     # ingest_vector_store_files — adapter mỏng gọi IngestionService
  utils/                  # config_loader, datetime_utils, io, key_generator, helper vector_store
config/config.yaml        # tunable được version control: batch size/concurrency embedding,
                          # tên bucket, storage.io_thread_pool_size,
                          # ingestion.cpu_thread_pool_size, ingestion.download_concurrency
docker/                   # Dockerfile + compose_db.yml / compose_web.yml / compose_tracking.yml
examples/file_upload_example.py  # demo end-to-end dùng SDK openai
```

---

# Topology Docker Compose

Ba file compose được `Makefile` gộp lại:

- **`compose_db.yml`** — `postgres` (5432), `redis` (6379), `qdrant` (6333 HTTP / 6334 gRPC)
- **`compose_tracking.yml`** — `minio` (9000 API / 9001 console) — object storage, dù tên file gợi ý khác
- **`compose_web.yml`** — `web` (uvicorn, 8005; phụ thuộc `postgres` khoẻ mạnh + `worker` đã khởi động) và `worker` (TaskIQ; phụ thuộc `postgres`, `redis`, `minio` khoẻ mạnh)

Bản thân Langfuse **không** nằm trong stack Compose này — cấu hình trỏ tới một instance bên ngoài/self-hosted. Tương tự với embedding server — `DENSE_EMBEDDING_URL` mặc định là `http://172.17.0.1:8100/v1` (máy host, không phải một service Compose). Giá trị mặc định đó chính là port được publish của [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService), một stack Compose riêng dựa trên vLLM chạy trên máy có GPU (`make up dense`, hoặc `make up hybrid` để có `:8100` dense + `:8101` sparse); việc để nó nằm ngoài stack này chính là thứ cho phép máy GPU và máy chạy API là hai máy khác nhau. Xem [Embedding Server](README_vi.md#embedding-server).

Cả `web` và `worker` chạy từ cùng một image (`docker/Dockerfile`, build multi-stage `uv sync --frozen`, user `appuser` không phải root), chỉ khác lệnh entrypoint (`uvicorn app.app:app` so với `taskiq worker app.tasks.broker:broker`). Mỗi tiến trình chạy độc lập cùng một quy trình bootstrap trong `app/startup.py`, nên cả hai đều có cùng bộ service sống, truy cập qua cùng các getter.

---

# Cấu hình

Xem [CONFIGURATION.md](../CONFIGURATION.md) để có bảng tham số đầy đủ: `.env` (hạ tầng/bí mật/công tắc provider) so với `config/config.yaml` (tunable tĩnh) so với giá trị mặc định trong code.

---

> Phân tích từng layer và tham chiếu API: [DETAILED_COMPONENTS_vi.md](DETAILED_COMPONENTS_vi.md)
