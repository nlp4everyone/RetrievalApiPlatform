# 🚀 RetrievalApiPlatform — Retrieval Engine cho hệ thống RAG

Một **Retrieval Engine** tương thích OpenAI API dành cho các hệ thống Retrieval-Augmented Generation (RAG), xây dựng trên FastAPI. Dự án cung cấp các endpoint `Files` và `Vector Stores` tương thích đủ gần với OpenAI để có thể dùng thẳng SDK chính thức của OpenAI, phía sau là một vector database có thể thay thế (hiện tại là Qdrant), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (ingestion nền), và Langfuse/OpenTelemetry (tracing).

<br />

## Tính năng chính

- **API tương thích OpenAI** — `/v1/files` và `/v1/vector_stores` mô phỏng sát OpenAI Files API và Vector Stores API, đến mức SDK `openai` Python gốc chạy được thẳng với server này mà không cần sửa (xem `examples/file_upload_example.py`)
- **Pipeline theo stage** — ingestion (`download → parse → chunk → embed → index`) và retrieval (`embed_query → retrieve → fuse`) đều được ghép từ các class `BaseStage` do một `Pipeline` chung thực thi. Thêm một bước là thêm một class, không phải sửa hàm điều phối
- **Provider có thể thay thế ở mọi lớp** — parsing, chunking, embedding và vector database đều nằm sau một interface `base.py`, với thư mục `provider/` và một facade `from_settings()`, chọn bằng đúng một biến môi trường
- **Ingestion bất đồng bộ** — upload file trả về ngay lập tức; pipeline chạy nền trên một TaskIQ worker (broker Redis Streams)
- **Vector search không phụ thuộc backend** — dense similarity search qua `BaseAsyncVectorStore`; Qdrant đã triển khai, Milvus đã được nối dây đầy đủ nhưng còn là placeholder. Metadata filter được biểu diễn dưới dạng cây trung lập rồi dịch riêng cho từng backend
- **Tracing bám theo luồng xử lý** — chính `Pipeline` (không phải từng stage) mở span, nên hình dạng trace trên Langfuse luôn đúng khi stage thay đổi. W3C trace context được truyền sang worker, nên các observation của ingestion nằm trong đúng trace của HTTP request đã tạo ra nó

<br />

## Yêu cầu trước khi cài đặt (Prerequisites)

1. **Software**
   - Docker và Docker Compose
   - Một endpoint dense embedding — hoặc tương thích OpenAI (ví dụ vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B` tại `VLLM_DENSE_EMBEDDING_URL`), hoặc một server Text Embeddings Inference tại `TEI_EMBEDDING_URL`
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
# chỉnh .env: API key, credential Postgres/MinIO/Qdrant/Langfuse, endpoint embedding,
#             và các công tắc provider (EMBEDDING_PROVIDER, CHUNKING_PROVIDER,
#             PDF_PARSER_PROVIDER, VECTOR_STORE_PROVIDER)
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

Upload trả về ngay lập tức; ingestion (download → parse → chunk → embed → index) chạy bất đồng bộ ở nền. Poll `GET /v1/vector_stores/{id}` và theo dõi `status` chuyển từ `in_progress` → `completed` (hoặc `failed`).

<br />

## Tích hợp (Integrations)

| Thành phần | Triển khai | Chọn bằng |
|---|---|---|
| API layer | FastAPI | — |
| Vector database | Qdrant (Milvus còn là stub) | `VECTOR_STORE_PROVIDER` |
| Metadata store | PostgreSQL (`asyncpg`) | — |
| Object storage | MinIO (lưu byte của file đã upload) | — |
| Task queue | Redis Streams + TaskIQ | — |
| Parsing | decoder in-process (`.txt`/`.md`), LlamaParse (`.pdf`) | `PDF_PARSER_PROVIDER` |
| Chunking | [Chonkie](https://docs.chonkie.ai) hoặc `langchain_text_splitters` | `CHUNKING_PROVIDER` |
| Embeddings | endpoint tương thích OpenAI hoặc Text Embeddings Inference | `EMBEDDING_PROVIDER` |
| Tracing | Langfuse qua OpenTelemetry OTLP | — |
| Runtime | Docker Compose | — |

<br />

## Tài liệu (Documentation)

- [Technical Overview](TECHNICAL_OVERVIEW_vi.md) — sơ đồ kiến trúc, cấu trúc repository, topology Docker Compose
- [Detailed Components](DETAILED_COMPONENTS_vi.md) — phân tích chi tiết từng layer api/service/pipeline/component/db
- [Flow](FLOW_vi.md) — sơ đồ từng bước cho upload, ingestion, và search
- [Design Decisions](DESIGN_DECISIONS_vi.md) — lý do thiết kế và các giới hạn hiện tại
- [Configuration Reference](../CONFIGURATION.md) — toàn bộ setting, nguồn cấu hình, và giá trị mặc định

<br />

## Giới hạn đã biết (Known Gaps)

Xem chi tiết tại [Design Decisions](DESIGN_DECISIONS_vi.md).

- Vector store chỉ ingest được đúng **một file**; nhiều hơn một `file_id` giờ bị từ chối ngay tại thời điểm request với lỗi 400, thay vì bị âm thầm bỏ qua
- `ranking_options` trên `POST /v1/vector_stores/{id}/search` được schema chấp nhận nhưng chưa được áp dụng (`filters` **đã** được áp dụng)
- Mới chỉ có `SearchType.DENSE` được triển khai — retrieval kiểu keyword/BM25 và hybrid đã có sẵn seam (`BaseRetriever`, `BaseFusion`) nhưng chưa có implementation
- Milvus đã được nối dây qua config, `VectorStoreType` và `VectorStoreFactory`, nhưng mọi method đều raise `NotImplementedError`
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
- [x] Trừu tượng hoá provider cho parsing, chunking, embedding và vector store
- [x] Khung pipeline theo stage cho ingestion và retrieval
- [x] Áp dụng metadata filter trong search
- [ ] Ingest nhiều file cho một vector store
- [ ] Hybrid search (retriever keyword/BM25 + chiến lược fusion)
- [ ] Áp dụng ranking option trong search
- [ ] Triển khai backend Milvus
- [ ] Endpoint sub-resource cho vector store file (attach/list/detach)
- [ ] Parser cho `.docx` (extension được chấp nhận khi upload nhưng chưa có parser đăng ký)
