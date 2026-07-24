# 🚀 RetrievalApiPlatform — Retrieval Engine cho hệ thống RAG

Một **Retrieval Engine** tương thích OpenAI API dành cho các hệ thống Retrieval-Augmented Generation (RAG), xây dựng trên FastAPI. Dự án cung cấp các endpoint `Files` và `Vector Stores` tương thích đủ gần với OpenAI để có thể dùng thẳng SDK chính thức của OpenAI, phía sau là Qdrant (vector), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (ingest nền), và Langfuse/OpenTelemetry (tracing).

<br />

## Tính năng chính

- **API tương thích OpenAI** — `/v1/files` và `/v1/vector_stores` mô phỏng sát OpenAI Files API và Vector Stores API, đến mức SDK `openai` Python gốc chạy được thẳng với server này mà không cần sửa (xem `examples/file_upload_example.py`)
- **Ingest pipeline bất đồng bộ (async)** — upload file trả về ngay lập tức; việc parse, chunking, embedding và upsert vào Qdrant chạy nền trên một TaskIQ worker (broker Redis Streams)
- **Parser có thể mở rộng (pluggable)** — hỗ trợ `.txt`, `.md`, `.pdf` (qua LlamaParse) hiện tại, đăng ký qua `ParserFactory` theo phần mở rộng file
- **Chunking có thể cấu hình** — chunker dựa trên [Chonkie](https://docs.chonkie.ai) với các strategy `recursive` / `sentence` / `token` / `character`
- **Vector search** — dense embedding qua một endpoint OpenAI-compatible, similarity search trên Qdrant với một collection riêng cho mỗi vector store
- **Tracing đầy đủ** — pipeline ingest và search được instrument end-to-end bằng Langfuse (qua OTel OTLP export), bao gồm các span embedding/retrieve/upsert

<br />

## Yêu cầu trước khi cài đặt (Prerequisites)

1. **Software**
   - Docker và Docker Compose
   - Một endpoint embedding tương thích OpenAI (ví dụ vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B`) có thể truy cập tại `VLLM_DENSE_EMBEDDING_URL`
   - Một instance [Langfuse](https://langfuse.com) (self-hosted hoặc cloud) cho tracing

2. **Hardware**
   - Máy chủ Ubuntu/Linux với tối thiểu 8 CPU core và 8GB RAM để chạy các service trong repo này
   - GPU được khuyến nghị cho embedding server (repo này không cần GPU, chỉ gọi HTTP ra ngoài tới server đó)

<br />

## Quick Start

```bash
git clone -b retrieval/naive-rag https://github.com/nlp4everyone/RetrievalApiPlatform.git
cd RetrievalApiPlatform
cp .env.sample .env
# chỉnh .env: API key, credential Postgres/MinIO/Qdrant/Langfuse, VLLM_DENSE_EMBEDDING_URL
make up      # build và khởi động postgres, redis, qdrant, minio, worker, web
make logs    # xem log của web service
```

<br />

## Quick Start (Python Client)

`examples/file_upload_example.py` dùng SDK `openai` gốc, trỏ vào server này để upload một file và tạo vector store từ file đó:

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

Upload trả về ngay lập tức; ingest (parse → chunk → embed → upsert) chạy bất đồng bộ ở nền. Poll `GET /v1/vector_stores/{id}` và theo dõi `status` chuyển từ `in_progress` → `completed` (hoặc `failed`).

<br />

## Tích hợp (Integrations)

- **API layer**: FastAPI
- **Vector database**: Qdrant
- **Metadata store**: PostgreSQL (`asyncpg`)
- **Object storage**: MinIO (lưu byte của file đã upload)
- **Task queue**: Redis Streams + TaskIQ (worker ingest bất đồng bộ)
- **Chunking**: [Chonkie](https://docs.chonkie.ai)
- **PDF parsing**: LlamaParse
- **Embeddings**: client tương thích OpenAI (`AsyncOpenAI`) gọi tới endpoint vLLM self-hosted
- **Tracing**: Langfuse qua OpenTelemetry OTLP
- **Runtime**: Docker Compose

<br />

## Tài liệu (Documentation)

- [Technical Overview](TECHNICAL_OVERVIEW_vi.md) — sơ đồ kiến trúc, cấu trúc repository, topology Docker Compose
- [Detailed Components](DETAILED_COMPONENTS_vi.md) — phân tích chi tiết từng layer router/service/db/core
- [Flow](FLOW_vi.md) — sequence diagram cho upload, ingest, và search
- [Design Decisions](DESIGN_DECISIONS_vi.md) — lý do thiết kế và các giới hạn hiện tại
- [Configuration Reference](../CONFIGURATION.md) — toàn bộ setting, nguồn cấu hình, và giá trị mặc định

<br />

## Giới hạn đã biết (Known Gaps)

Xem chi tiết tại [Design Decisions](DESIGN_DECISIONS_vi.md).

- Vector store hiện chỉ ingest được đúng **một file**; các `file_ids` dư được API chấp nhận nhưng bị worker âm thầm bỏ qua
- `filters` và `ranking_options` trên `POST /v1/vector_stores/{id}/search` được schema chấp nhận nhưng chưa được áp dụng
- Auth dùng chung một `FASTAPI_API_KEY` duy nhất — chưa phải multi-tenant theo từng người dùng, dù các row đã được scope theo `api_key`
- Chưa có endpoint sub-resource "vector store files" kiểu OpenAI (attach/list/detach một file trên vector store đã tồn tại)

<br />

## To-Do / Roadmap

- [x] Base components, Chonkie chunking, naive search
- [x] Endpoint Files + Vector Stores tương thích OpenAI
- [x] Ingest bất đồng bộ qua TaskIQ + Redis
- [x] Parser PDF bằng LlamaParse (đã gỡ bỏ parser UndatasIO)
- [x] Tracing Langfuse/OpenTelemetry xuyên suốt ingest + search
- [x] Request ID correlation giữa HTTP và worker
- [ ] Ingest nhiều file cho một vector store
- [ ] Áp dụng metadata filter và ranking option trong search
- [ ] Endpoint sub-resource cho vector store file (attach/list/detach)
- [ ] Parser cho `.docx` (extension được chấp nhận khi upload nhưng chưa có parser đăng ký)
