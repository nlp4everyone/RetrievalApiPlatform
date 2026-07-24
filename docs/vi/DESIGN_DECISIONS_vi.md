# Design Decisions

## Vì sao xây API tương thích OpenAI

`/v1/files` và `/v1/vector_stores` được mô phỏng theo OpenAI Files API và Vector Stores API — cùng object shape, cùng error envelope, cùng kiểu auth. Đây là một lớp tương thích **partial** (không đầy đủ) có chủ đích, không phải thiếu sót — xem phần [Giới hạn đã biết](README_vi.md#giới-hạn-đã-biết-known-gaps) trong README_vi.md.

**Ưu điểm**
- Tương thích SDK có sẵn: bất kỳ tool nào đã xây trên SDK `openai` — framework RAG, agent SDK, script nội bộ — chỉ cần đổi `base_url`, không cần viết client riêng.
- Giảm chi phí học: người dùng đã quen OpenAI Files/Vector Stores API không cần học một API mới.

**Nhược điểm**
- Bị trói theo hình dạng API của OpenAI: object shape, error envelope phải theo đúng OpenAI dù có thể không tối ưu cho nhu cầu riêng của repo.
- Tương thích chỉ partial dễ gây ngộ nhận: caller có thể tưởng nhầm là hỗ trợ đầy đủ như OpenAI (filters, ranking, multi-file...) trong khi thực tế chưa.

**Thay thế đã cân nhắc**

| Phương án | Lý do không chọn |
|---|---|
| Tự thiết kế REST API riêng | Không tận dụng được SDK/tooling có sẵn của hệ sinh thái OpenAI, tăng effort tích hợp cho người dùng |
| GraphQL hoặc RPC riêng | Không có SDK/adapter sẵn có cho hệ sinh thái RAG hiện tại, phải tự viết toàn bộ client |

## Vì sao dùng Background Worker qua TaskIQ

Ingest một file (parse → chunk → embed → upsert) có thể mất từ vài giây đến hàng chục giây, đặc biệt với PDF qua LlamaParse hoặc file lớn — quá lâu để giữ một request HTTP chờ đồng bộ. `POST /v1/vector_stores` chỉ tạo record với `status=in_progress` rồi enqueue job; việc ingest thật sự chạy trên một worker process riêng (TaskIQ, broker Redis Streams), tách hẳn khỏi vòng đời request.

**Ưu điểm**
- Request trả lời ngay và có thời gian phản hồi dự đoán được: API process không bị block chờ job nặng CPU/IO.
- Scale độc lập: `web` và `worker` là hai process khác nhau, có thể thêm worker replica khi ingest nghẽn mà không cần scale API.
- Chịu lỗi tốt hơn: job nằm trong queue, worker crash hoặc restart không kéo theo mất request của caller.
- Đúng tinh thần API gốc: OpenAI cũng tạo vector store theo kiểu bất đồng bộ kèm polling, nên hành vi này không phải một khác biệt so với API gốc.

**Nhược điểm**
- Caller không biết kết quả ngay: phải chủ động poll `GET /v1/vector_stores/{id}` thay vì nhận response đồng bộ.
- Thêm một thành phần hạ tầng phải vận hành và giám sát riêng (broker Redis, worker process), thay vì mọi thứ nằm gọn trong một process API.

**Thay thế đã cân nhắc**

| Phương án | Lý do không chọn |
|---|---|
| Ingest đồng bộ ngay trong request | Dễ timeout HTTP với file lớn/PDF phức tạp; block luôn API process dưới tải cao |
| Background task nội bộ của FastAPI (`BackgroundTasks`) | Không tách được process, không scale độc lập với API; job bị mất nếu process API restart hoặc crash |

## Vì sao dùng Qdrant làm vector database

Mỗi vector store trong repo tương ứng một collection riêng trong Qdrant, nơi lưu embedding và thực hiện similarity search khi gọi `/v1/vector_stores/{id}/search`.

**Ưu điểm**
- Hỗ trợ sẵn sparse vector / BM25: Qdrant cho phép định nghĩa `sparse_vectors_config` song song với dense vector trên cùng một collection — mở đường nâng cấp lên hybrid search (dense + BM25) sau này mà không cần đổi vector database, chỉ cần bật config đã có sẵn trong wrapper hiện tại.
- Per-collection isolation tự nhiên: mỗi vector store là một collection riêng, khớp thẳng với mô hình dữ liệu của repo — xoá vector store thì xoá cascade đúng một collection, không lẫn dữ liệu giữa các vector store.
- Client Python async chính thức (`AsyncQdrantClient`), khớp với stack async toàn repo (FastAPI, asyncpg, TaskIQ) mà không cần wrapper sync-to-async.
- Hỗ trợ sẵn quantization (binary/scalar/product) và lưu vector on-disk, giúp scale dung lượng lớn mà không phải tự implement nén vector.

**Nhược điểm**
- Thêm một service riêng phải vận hành trong Docker Compose stack, ngoài Postgres/MinIO/Redis đã có.
- Ở giai đoạn hiện tại (naive-rag), sparse/BM25 mới chỉ là hook trong wrapper (`sparse_vectors_config`), chưa được bật trong pipeline ingest thật — lợi ích hybrid search hiện vẫn là tiềm năng, chưa hiện thực hoá.

**Thay thế đã cân nhắc**

| Phương án | Lý do không chọn |
|---|---|
| pgvector (extension của Postgres) | Không có native BM25/sparse vector; phải tự ghép full-text search riêng, mất luôn lợi thế "một collection cho cả dense lẫn sparse" |
| Elasticsearch / OpenSearch | Mạnh về BM25/full-text nhưng vector search không tối ưu bằng Qdrant cho similarity search thuần, và nặng hơn để vận hành cho một service chỉ cần vector store |
