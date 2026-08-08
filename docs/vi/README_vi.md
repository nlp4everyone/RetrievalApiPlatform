# 🚀 RetrievalApiPlatform — Retrieval Engine cho hệ thống RAG

Một **Retrieval Engine** tương thích OpenAI API dành cho các hệ thống Retrieval-Augmented Generation (RAG), xây dựng trên FastAPI. Dự án cung cấp các endpoint `Files` và `Vector Stores` tương thích đủ gần với OpenAI để có thể dùng thẳng SDK chính thức của OpenAI, phía sau là một vector database có thể thay thế (hiện tại là Qdrant), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (ingestion nền), và Langfuse/OpenTelemetry (tracing).

<br />

## Tính năng chính

- **API tương thích OpenAI** — `/v1/files` và `/v1/vector_stores` mô phỏng sát OpenAI Files API và Vector Stores API, đến mức SDK `openai` Python gốc chạy được thẳng với server này mà không cần sửa (xem `examples/file_upload_example.py`)
- **Pipeline theo stage** — ingestion (`download → parse → chunk → embed_index`) và retrieval (`embed_query → retrieve → fuse`) đều được ghép từ các class `BaseStage` do một `Pipeline` chung thực thi. Thêm một bước là thêm một class, không phải sửa hàm điều phối
- **Ingestion streaming, bộ nhớ có trần** — bước cuối embed và upsert **từng batch một** trong cùng một semaphore, nên việc ghi bắt đầu từ batch đầu tiên và bộ nhớ đỉnh là `batch_size × concurrency` chứ không theo kích thước file. Worker tách pool thread I/O khỏi pool CPU và chặn trần số download song song
- **Provider có thể thay thế ở mọi lớp** — parsing, chunking, embedding và vector database đều nằm sau một interface `base.py`, với thư mục `provider/` và một facade `from_settings()`, chọn bằng đúng một biến môi trường
- **Ingestion bất đồng bộ** — upload file trả về ngay lập tức; pipeline chạy nền trên một TaskIQ worker (broker Redis Streams)
- **Vector search không phụ thuộc backend** — dense similarity search qua `BaseAsyncVectorStore`; Qdrant đã triển khai, Milvus đã được nối dây đầy đủ nhưng còn là placeholder. Metadata filter được biểu diễn dưới dạng cây trung lập rồi dịch riêng cho từng backend
- **Tracing bám theo luồng xử lý** — chính `Pipeline` (không phải từng stage) mở span, nên hình dạng trace trên Langfuse luôn đúng khi stage thay đổi. W3C trace context được truyền sang worker, nên các observation của ingestion nằm trong đúng trace của HTTP request đã tạo ra nó

<br />

## Yêu cầu trước khi cài đặt (Prerequisites)

1. **Software**
   - Docker và Docker Compose
   - Python 3.11–3.13 nếu chạy ngoài Docker (`requires-python = ">=3.11,<3.14"`, theo ràng buộc của `unstructured`)
   - Một endpoint dense embedding tại `DENSE_EMBEDDING_URL` — hoặc tương thích OpenAI (ví dụ vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B`), hoặc một server Text Embeddings Inference. [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) là repo đồng hành phục vụ đúng phần này, trên chính các port mà repo này mặc định trỏ tới — xem [Embedding Server](#embedding-server)
   - API key cho các service parsing thực sự dùng: `LLAMAPARSE_API_KEY` cho PDF, `UNSTRUCTURED_API_KEY` (+ `UNSTRUCTURED_API_URL`) cho mọi định dạng còn lại. Key chỉ được kiểm tra khi provider tương ứng được dùng lần đầu, nên deployment chỉ ingest PDF không cần key của Unstructured
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
#             key parsing (LLAMAPARSE_API_KEY, UNSTRUCTURED_API_KEY/UNSTRUCTURED_API_URL),
#             và các công tắc provider (EMBEDDING_PROVIDER, CHUNKING_PROVIDER,
#             PDF_PARSER_PROVIDER, VECTOR_STORE_PROVIDER)
make up      # build và khởi động postgres, redis, qdrant, minio, worker, web
make logs    # xem log của web service
```

Endpoint embedding phải sẵn sàng *trước* khi chạy `make up` — quá trình startup có thăm dò nó và sẽ fail nếu không kết nối được. Xem [Embedding Server](#embedding-server) bên dưới.

<br />

## Embedding Server

Repo này không tự tính vector — mọi embedding đều là một lời gọi HTTP tới model server chạy riêng, và đó là lý do `DENSE_EMBEDDING_URL` mặc định là `http://172.17.0.1:8100/v1` (máy host của Docker, không phải một service Compose). [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) là repo đồng hành cho phần đó: vLLM đứng sau một API `/v1/embeddings` tương thích OpenAI, đã dựng sẵn đúng hai model mà repo này mặc định dùng — `Qwen/Qwen3-Embedding-0.6B` (dense, 1024 chiều) và `BAAI/bge-m3` (sparse). Port mặc định của nó cũng chính là port phía này chờ sẵn, nên hai bên khớp nhau mà không cần cấu hình thêm.

```bash
git clone -b engine/vllm https://github.com/nlp4everyone/EmbeddingService.git
cd EmbeddingService
cp .env.sample .env   # SERVING_API_KEY phải trùng với DENSE_EMBEDDING_API_KEY của repo này
make up dense         # chỉ dense           → :8100
# make up hybrid      # dense + sparse      → :8100 + :8101, bắt buộc nếu SPARSE_EMBEDDING_ENABLED=true
make status           # health check → OK
make test             # gửi thử một request /v1/embeddings
```

Sau đó trỏ repo này sang nó:

| Bên này (`.env`) | Bên kia (`.env`) | Mặc định |
|---|---|---|
| `DENSE_EMBEDDING_URL` | `VLLM_DENSE_EMBEDDING_PORT` | `http://172.17.0.1:8100/v1` ← `8100` |
| `SPARSE_EMBEDDING_URL` | `VLLM_SPARSE_EMBEDDING_PORT` | `http://172.17.0.1:8101` ← `8101` |
| `DENSE_MODEL_NAME` | `DENSE_MODEL_NAME` | `Qwen/Qwen3-Embedding-0.6B` |
| `SPARSE_MODEL_NAME` | `SPARSE_MODEL_NAME` | `BAAI/bge-m3` |
| `DENSE_EMBEDDING_API_KEY` / `SPARSE_EMBEDDING_API_KEY` | `SERVING_API_KEY` | phải trùng nhau |

Lưu ý:

- `EMBEDDING_PROVIDER=openai` là provider nói chuyện với nó; `tei` dành cho server Text Embeddings Inference. Phía sparse dùng `SPARSE_EMBEDDING_PROVIDER=vllm`, đọc token id từ `/tokenize` và trọng số từ `/pooling` của vLLM — đều là endpoint vLLM có sẵn, nên `make up sparse`/`hybrid` không cần thêm gì
- Yêu cầu GPU Nvidia (Compute Capability 7.0+, ≥8GB VRAM), driver Nvidia 535.54.03+ và Nvidia Container Toolkit. Hãy chạy nó trên máy có GPU, còn repo này chạy ở bất cứ đâu gọi được HTTP tới đó
- Chạy cả hai model trên cùng một GPU thì VRAM được chia theo `DENSE_GPU_MEM_UTIL`/`SPARSE_GPU_MEM_UTIL` (mặc định `0.6`/`0.3`) — hãy chỉnh cho vừa card của bạn
- Không có ràng buộc cứng nào với repo đó: bất kỳ endpoint tương thích OpenAI hoặc TEI nào cũng dùng được. Đây chỉ là cặp đã kiểm chứng là chạy tốt

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

Upload trả về ngay lập tức; ingestion (download → parse → chunk → embed_index) chạy bất đồng bộ ở nền. Poll `GET /v1/vector_stores/{id}` và theo dõi `status` chuyển từ `in_progress` → `completed` (hoặc `failed`).

<br />

## Tích hợp (Integrations)

| Thành phần | Triển khai | Chọn bằng |
|---|---|---|
| API layer | FastAPI | — |
| Vector database | Qdrant (Milvus còn là stub) | `VECTOR_STORE_PROVIDER` |
| Metadata store | PostgreSQL (`asyncpg`) | — |
| Object storage | MinIO (lưu byte của file đã upload) | — |
| Task queue | Redis Streams + TaskIQ | — |
| Parsing | LlamaParse (`.pdf`), Unstructured API (`.txt`, `.md`, `.docx`, `.doc`, ảnh) — cả hai đều trả Markdown | `PDF_PARSER_PROVIDER` (chỉ cho PDF) |
| Chunking | [Chonkie](https://docs.chonkie.ai) hoặc `langchain_text_splitters` | `CHUNKING_PROVIDER` |
| Embeddings | endpoint tương thích OpenAI hoặc Text Embeddings Inference — ví dụ [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) (vLLM) | `EMBEDDING_PROVIDER` |
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
- Trong `ranking_options` của `POST /v1/vector_stores/{id}/search`, mới chỉ `score_threshold` được áp dụng; `ranker` và `rewrite_query` vẫn được nhận rồi bỏ qua (`filters` **đã** được áp dụng)
- Hybrid search (dense + sparse) chỉ chạy khi có đủ cả hai nửa: `SPARSE_EMBEDDING_ENABLED` **và** collection đã ingest kèm sparse vector. Store tạo trước khi bật sparse vẫn dense-only — Qdrant không thêm được field vector vào collection đang sống, nên muốn hybrid thì phải ingest lại. `search_type: "auto"` (mặc định) tự phân giải theo từng store; còn đòi `"hybrid"` trên store không đáp ứng được thì nhận lỗi 400 chứ không âm thầm rơi về dense
- Milvus đã được nối dây qua config, `VectorStoreType` và `VectorStoreFactory`, nhưng mọi method đều raise `NotImplementedError`
- Auth dùng chung một `FASTAPI_API_KEY` duy nhất — chưa phải multi-tenant theo từng người dùng, dù các row đã được scope theo `api_key`
- Chưa có endpoint sub-resource "vector store files" kiểu OpenAI (attach/list/detach một file trên vector store đã tồn tại)
- `.csv`, `.json` và `.gif` vượt qua validation lúc upload nhưng chưa có provider parsing nào đăng ký; ngược lại `.md` và `.doc` parse được nhưng không nằm trong allow-list lúc upload nên luôn bị 415

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
- [x] Parsing qua Unstructured API cho `.txt`/`.md`/`.docx`/`.doc`/ảnh (thay decoder in-process; mọi định dạng giờ đều ra Markdown)
- [x] Ingestion streaming: gộp embed + index thành một stage, bộ nhớ đỉnh không phụ thuộc kích thước file
- [x] Tách pool thread I/O và CPU, chặn trần download đồng thời trong worker
- [ ] Ingest nhiều file cho một vector store
- [x] Hybrid search (sparse vector BGE-M3, trộn với dense bằng RRF của Qdrant)
- [x] `search_type` theo từng request (`auto`/`dense`/`hybrid`), chỉ định hybrid trên store không đáp ứng được thì bị từ chối bằng 400
- [ ] Áp dụng nốt phần còn lại của `ranking_options` trong search (`score_threshold` xong; `ranker`, `rewrite_query` vẫn bị bỏ qua)
- [ ] Triển khai backend Milvus
- [ ] Endpoint sub-resource cho vector store file (attach/list/detach)
- [ ] Đồng bộ hai allow-list: parser cho `.csv`/`.json`/`.gif` (hoặc gỡ chúng khỏi upload), và thêm `.md`/`.doc` vào `ALLOWED_EXTENSIONS`
