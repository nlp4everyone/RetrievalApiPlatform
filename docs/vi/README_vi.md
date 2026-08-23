# 🚀 RetrievalApiPlatform — Retrieval Engine cho hệ thống RAG

Một **Retrieval Engine** tương thích OpenAI API dành cho các hệ thống Retrieval-Augmented Generation (RAG), xây dựng trên FastAPI. Dự án cung cấp các endpoint `Files` và `Vector Stores` tương thích đủ gần với OpenAI để có thể dùng thẳng SDK chính thức của OpenAI, phía sau là một vector database có thể thay thế (Qdrant hoặc Milvus, hoặc cả hai cùng lúc), Postgres (metadata), MinIO (object storage), Redis + TaskIQ (ingestion nền), và Langfuse/OpenTelemetry (tracing).

<br />

## Tính năng chính

- **API tương thích OpenAI** — `/v1/files` và `/v1/vector_stores` mô phỏng sát OpenAI Files API và Vector Stores API, đến mức SDK `openai` Python gốc chạy được thẳng với server này mà không cần sửa (xem `examples/file_upload_example.py`)
- **Pipeline theo stage** — ingestion (`download → parse → persist_text → chunk → embed_index`) và retrieval (`embed_query → retrieve → fuse`) đều được ghép từ các class `BaseStage` do một `Pipeline` chung thực thi. Thêm một bước là thêm một class, không phải sửa hàm điều phối
- **Ingestion streaming, bộ nhớ có trần** — bước cuối embed và upsert **từng batch một** trong cùng một semaphore, nên việc ghi bắt đầu từ batch đầu tiên và bộ nhớ đỉnh là `batch_size × concurrency` chứ không theo kích thước file. Worker tách pool thread I/O khỏi pool CPU và chặn trần số thao tác MinIO song song
- **Provider có thể thay thế ở mọi lớp** — parsing, chunking, embedding và vector database đều nằm sau một interface `base.py`, với thư mục `provider/` và một facade `from_settings()`, chọn bằng đúng một biến môi trường
- **Ingestion bất đồng bộ** — upload file trả về ngay lập tức; pipeline chạy nền trên một TaskIQ worker (broker Redis Streams)
- **Vector search không phụ thuộc backend** — dense và hybrid search qua `BaseAsyncVectorStore`; cả Qdrant lẫn Milvus đều đã triển khai và có thể kết nối cùng lúc, vì mỗi vector store đều nhớ engine nào đang giữ nó. Metadata filter được biểu diễn dưới dạng cây trung lập rồi dịch riêng cho từng backend
- **Tracing bám theo luồng xử lý** — chính `Pipeline` (không phải từng stage) mở span, nên hình dạng trace trên Langfuse luôn đúng khi stage thay đổi. W3C trace context được truyền sang worker, nên các observation của ingestion nằm trong đúng trace của HTTP request đã tạo ra nó

<br />

## Yêu cầu trước khi cài đặt (Prerequisites)

1. **Software**
   - Docker và Docker Compose
   - Python 3.11–3.13 nếu chạy ngoài Docker (`requires-python = ">=3.11,<3.14"`, theo ràng buộc của `unstructured`)
   - Một vector database, nằm ngoài stack này: [Qdrant](https://qdrant.tech/documentation/guides/installation/) `v1.17`+ (mặc định — dùng `QDRANT_URL` + `QDRANT_API_KEY`) hoặc [Milvus](https://milvus.io/docs/install_standalone-docker.md) `2.4`+ (dùng `MILVUS_URI`, thêm `MILVUS_TOKEN` khi bật xác thực). `make up` không đụng tới nó, nên redeploy không làm mất index
   - Một endpoint dense embedding tại `DENSE_EMBEDDING_URL` — hoặc tương thích OpenAI (ví dụ vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B`), hoặc một server Text Embeddings Inference. [`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService) là repo đồng hành phục vụ đúng phần này, trên chính các port mà repo này mặc định trỏ tới
   - API key cho các service parsing thực sự dùng: `LLAMAPARSE_API_KEY` cho PDF, `UNSTRUCTURED_API_KEY` (+ `UNSTRUCTURED_API_URL`) cho mọi định dạng còn lại. Key chỉ được kiểm tra khi provider tương ứng được dùng lần đầu, nên deployment chỉ ingest PDF không cần key của Unstructured
   - Một instance [Langfuse](https://langfuse.com) (self-hosted hoặc cloud) cho tracing

2. **Hardware**
   - Máy chủ Ubuntu/Linux với tối thiểu 8 CPU core và 8GB RAM để chạy các service trong repo này
   - GPU được khuyến nghị cho embedding server (repo này không cần GPU, chỉ gọi HTTP ra ngoài tới server đó)

<br />

## Quick Start

**1. Khởi động stack.**

```bash
git clone https://github.com/nlp4everyone/RetrievalApiPlatform.git
cd RetrievalApiPlatform
cp .env.sample .env
# chỉnh .env: API key, credential Postgres/MinIO/Qdrant/Langfuse, endpoint embedding,
#             key parsing (LLAMAPARSE_API_KEY, UNSTRUCTURED_API_KEY/UNSTRUCTURED_API_URL),
#             và các công tắc provider (EMBEDDING_PROVIDER, PDF_PARSER_PROVIDER,
#             VECTOR_STORE_PROVIDER)
make up      # build và khởi động postgres, redis, minio, worker, web
make logs    # xem log của web service
```

Vector store và endpoint embedding đều phải sẵn sàng *trước* khi chạy `make up` — startup có thăm dò cả hai và sẽ fail nếu thiếu một trong hai, nên `QDRANT_URL`/`QDRANT_API_KEY` phải khớp với Qdrant bạn đang chạy. Phần embedding xem [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService).

**2. Upload file và tạo vector store.** `examples/file_upload_example.py` dùng SDK `openai` gốc, trỏ vào server này:

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

Upload trả về ngay lập tức; ingestion (download → parse → persist_text → chunk → embed_index) chạy bất đồng bộ ở nền. Poll `GET /v1/vector_stores/{id}` và theo dõi `status` chuyển từ `in_progress` → `completed` (hoặc `failed`).

*Tuỳ chọn:* kiểu chunking được tự suy ra từ tài liệu (Markdown nếu có heading, còn lại dùng recursive). Muốn tự chọn thì thêm `extra_body={"chunking_splitter": "recursive"}` — một trong `markdown`, `recursive`, `sentence`, `token`, `character`. Lưu ý `recursive` không có overlap, nên đi kèm `chunk_overlap_tokens` khác 0 sẽ bị `422`.

**3. Search và in kết quả.** Khi store đã `completed`:

```python
results = client.vector_stores.search(
    vector_store_id=vector_store.id,
    query="Thời hạn hoàn tiền là bao lâu?",
    max_num_results=5,
)

for hit in results.data:
    print(f"{hit.score:.3f}  {hit.filename}")
    print(hit.content[0].text[:300], "\n")
```

Mỗi hit gồm `score`, `file_id`/`filename`, `attributes` của file, và `content` là các chunk text. `search_type` mặc định là `auto` — hybrid khi store có sparse vector, còn lại là dense; muốn ép thì dùng `extra_body={"search_type": "hybrid"}`, và store không có sparse sẽ trả 400.

<br />

## Tích hợp (Integrations)

| Thành phần | Triển khai | Chọn bằng |
|---|---|---|
| API layer | FastAPI | — |
| Vector database | Qdrant và Milvus, kết nối được cùng lúc | `VECTOR_STORE_PROVIDER` (+ credential của từng backend) |
| Metadata store | PostgreSQL (`asyncpg`) | — |
| Object storage | MinIO (lưu byte của file đã upload) | — |
| Task queue | Redis Streams + TaskIQ | — |
| Parsing | LlamaParse (`.pdf`), Unstructured API (`.txt`, `.md`, `.docx`, `.doc`, ảnh) — cả hai đều trả Markdown | `PDF_PARSER_PROVIDER` (chỉ cho PDF) |
| Chunking | [Chonkie](https://docs.chonkie.ai) hoặc `langchain_text_splitters` | `chunking_splitter` theo từng request, không có thì tự suy ra từ tài liệu |
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
- [x] Tách pool thread I/O và CPU, chặn trần thao tác MinIO đồng thời trong worker
- [x] Lưu lại bản Markdown đã parse và dùng làm cache parse theo từng tài khoản, nên ingest lại không phải trả tiền cho vendor parse lần nữa
- [ ] Bảng `vector_store_files` cho trạng thái theo từng file (điều kiện tiên quyết của cả hai mục dưới)
- [ ] Ingest nhiều file cho một vector store
- [x] Hybrid search (sparse vector BGE-M3, trộn với dense bằng RRF của Qdrant)
- [x] `search_type` theo từng request (`auto`/`dense`/`hybrid`), chỉ định hybrid trên store không đáp ứng được thì bị từ chối bằng 400
- [ ] Áp dụng nốt phần còn lại của `ranking_options` trong search (`score_threshold` xong; `ranker`, `rewrite_query` vẫn bị bỏ qua)
- [x] Triển khai backend Milvus, kết nối song song được với Qdrant
- [ ] Endpoint sub-resource cho vector store file (attach/list/detach)
- [ ] Đồng bộ hai allow-list: parser cho `.csv`/`.json`/`.gif` (hoặc gỡ chúng khỏi upload), và thêm `.md`/`.doc` vào `ALLOWED_EXTENSIONS`
- [ ] Test tự động (`pytest` đã là dependency dev; chưa có gì dùng tới)
