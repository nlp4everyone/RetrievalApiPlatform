# Design Decisions

## Vì sao chọn API tương thích OpenAI

`/v1/files` và `/v1/vector_stores` được mô phỏng theo OpenAI Files API và Vector Stores API — cùng hình dạng object, cùng error envelope, cùng kiểu auth. Đây là lớp tương thích **một phần** có chủ đích, không phải thiếu sót — xem phần Giới hạn đã biết bên dưới.

**Ưu điểm**
- Tương thích SDK sẵn có: mọi công cụ đã xây trên SDK `openai` — RAG framework, agent SDK, script nội bộ — chỉ cần đổi `base_url`, không cần client riêng.
- Chi phí học thấp: người đã quen OpenAI Files/Vector Stores API không phải học một API mới.

**Nhược điểm**
- Bị khoá vào hình dạng API của OpenAI: hình dạng object và error envelope phải theo OpenAI kể cả khi điều đó không phù hợp nhất với nhu cầu riêng của repo.
- Tương thích một phần dễ gây hiểu lầm: caller có thể tưởng đã ngang bằng hoàn toàn với OpenAI (ranking option, multi-file...) trong khi thực tế thì chưa.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Thiết kế REST API riêng | Mất toàn bộ SDK/tooling đã dựng quanh hệ sinh thái OpenAI, tăng chi phí tích hợp cho người dùng |
| API GraphQL hoặc RPC riêng | Không có SDK/adapter sẵn cho hệ sinh thái RAG hiện tại, phải viết client từ đầu |

## Vì sao dùng background worker qua TaskIQ

Ingest một file (download → parse → chunk → embed → index) có thể mất từ vài giây tới vài chục giây, đặc biệt với PDF qua LlamaParse hoặc file lớn — quá lâu để giữ một HTTP request mở đồng bộ. `POST /v1/vector_stores` chỉ tạo record với `status=in_progress` và đẩy job; phần việc thật chạy trên một tiến trình worker riêng (TaskIQ, broker Redis Streams), tách khỏi vòng đời request.

**Ưu điểm**
- Request trả về ngay với thời gian phản hồi dự đoán được: tiến trình API không bao giờ bị chặn bởi công việc nặng CPU/IO.
- Scale độc lập: `web` và `worker` là hai tiến trình riêng, nên có thể tăng replica worker khi ingestion dồn ứ mà không phải scale API.
- Chịu lỗi tốt hơn: job nằm trong queue, nên worker crash hay restart không làm mất request của caller.
- Đúng tinh thần API gốc: việc tạo vector store của chính OpenAI cũng bất đồng bộ kèm polling, nên đây không phải sự lệch hướng.

**Nhược điểm**
- Caller không có kết quả ngay: phải chủ động poll `GET /v1/vector_stores/{id}` thay vì nhận response đồng bộ.
- Thêm một thành phần hạ tầng phải chạy và giám sát riêng (Redis broker, tiến trình worker), thay vì mọi thứ nằm trong một tiến trình API duy nhất.
- Observability khó hơn: nếu không xử lý thêm, span của job ingestion sẽ tạo thành một trace tách rời khỏi request đã sinh ra nó. Đó là lý do W3C trace context được inject vào payload của task và extract lại ở phía worker.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Ingest đồng bộ ngay trong request | Dễ timeout HTTP với file lớn/PDF phức tạp; chặn tiến trình API khi tải cao |
| `BackgroundTasks` có sẵn của FastAPI | Không tách tiến trình, không scale độc lập được với API; job mất khi tiến trình API restart hoặc crash |

## Vì sao dùng pipeline theo stage thay vì các hàm ingest/search

Ingestion và retrieval mỗi cái được biểu diễn thành một danh sách class `BaseStage` do một `Pipeline` chung thực thi, với một context object mutable xuyên suốt, thay vì một hàm thủ tục làm việc từ trên xuống.

**Ưu điểm**
- Thêm một bước là thêm một class cộng một dòng trong factory — embedding sparse/BM25, một lượt OCR, một bộ lọc trùng lặp, một reranker đều vào theo cùng một cách, không phải sửa code chạy chúng.
- Tracing thôi là mối bận tâm của từng hàm. `Pipeline.run()` là nơi duy nhất mở span, nên hình dạng trace là thuộc tính của pipeline và vẫn đúng khi stage được thêm, bớt hay đảo thứ tự — không stage nào quên instrument chính nó, và không stage nào import OpenTelemetry.
- Stage test được độc lập: mỗi stage đọc và ghi một dataclass thuần, không cần broker, không cần HTTP, không cần tracer.
- Hai pipeline dùng chung một runner, nên cải tiến về xử lý lỗi hay lồng span có lợi cho cả hai.

**Nhược điểm**
- Nhiều lớp gián tiếp hơn khi đọc: theo dõi một lượt ingestion từ đầu tới cuối nghĩa là mở một factory, một context và năm file stage thay vì một hàm duy nhất.
- Context mutable dùng chung là contract yếu hơn tham số tường minh — một stage về mặt kỹ thuật có thể đọc field mà stage trước chưa ghi, và chỉ fail lúc runtime.
- Chi phí này không đáng với một pipeline chỉ có hai bước; nó đáng ở đây vì cả hai pipeline đều được dự kiến sẽ mở rộng.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| `load_and_chunk_file()` / `embed_and_upload_chunks()` thủ tục (hình dạng cũ) | Mỗi bước mới đều phải sửa một hàm sẵn có, và mỗi bước phải tự nhớ mở span; hình dạng trace dần lệch khỏi hình dạng code |
| Framework điều phối bên thứ ba (Prefect, Dagster, LangChain chains) | Nặng hơn nhiều so với nhu cầu của một pipeline năm bước in-process, và nó sẽ chiếm quyền tích hợp tracing mà repo này muốn trỏ thẳng vào Langfuse |

## Vì sao mọi năng lực đều nằm sau một interface provider

Parsing, chunking, embedding và vector database đều theo cùng một khuôn: một interface `base.py`, một thư mục `provider/` chứa các implementation, và một facade có `from_settings()` dựng cái được đặt tên bởi một biến môi trường. Tên provider được validate trong `settings.py`, nên gõ sai sẽ fail lúc startup chứ không phải lúc dùng lần đầu.

**Ưu điểm**
- Đổi backend là sửa `.env`, không phải sửa code — hữu ích khi so sánh các chunker hoặc chuyển giữa embedding server tương thích OpenAI và TEI.
- Phần còn lại của codebase chỉ nhìn thấy interface, nên việc đổi backend không thể rò rỉ lên trên. `app/db/vector_store/types.py` bị cấm tường minh việc import SDK nhà cung cấp chính vì lý do này.
- Backend chưa cài không tốn gì: `VectorStoreFactory` định địa chỉ backend bằng đường dẫn module và import lúc dùng lần đầu, nên thiếu `pymilvus` chỉ thành vấn đề nếu Milvus thực sự được yêu cầu — và điều đó giữ đồ thị import không có chu trình.
- Một backend mới có thể được nối dây đầy đủ (config, enum, factory, startup) và merge trước cả khi nó chạy được, đúng như placeholder Milvus hiện tại: bật nó sau này chỉ là điền thân hàm, không đổi gì phía trên `app.db`.

**Nhược điểm**
- Vấn đề mẫu số chung nhỏ nhất: interface chỉ phơi bày được những gì mọi backend đều làm được, nên tính năng riêng của backend (sparse vector của Qdrant, quantization rescore) cần hoặc mở rộng contract hoặc một lối thoát riêng.
- Nhiều file hơn cho mỗi năng lực so với việc gọi thẳng, và thêm một lớp gián tiếp khi debug.
- Một backend placeholder raise `NotImplementedError` thì tra được trong config nhưng không dùng được — cấu hình sai lộ ra dưới dạng lỗi runtime chứ không phải lỗi startup.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Gọi thẳng SDK của từng nhà cung cấp tại nơi cần | Lựa chọn backend rò rỉ vào service và pipeline; đổi backend là phải sửa mọi call site |
| Một khối `if provider == ...` bên trong mỗi service | Ổn với hai provider và mục ruỗng ở bốn; đồng thời đặt import của mọi backend lên đường chạy nóng dù có dùng hay không |

## Vì sao vector store không phụ thuộc provider (với Qdrant là implementation)

Mỗi vector store trong repo ứng với một collection riêng, truy cập qua `BaseAsyncVectorStore` chứ không phải qua client Qdrant. Provider được ghi **trên chính row của vector store**, không đọc từ config lúc truy vấn.

**Ưu điểm**
- Collection cũ vẫn chạy sau khi `VECTOR_STORE_PROVIDER` đổi: `get_store()` nhận provider mà store đó được tạo cùng, nên đổi mặc định chỉ ảnh hưởng store mới.
- Filter được biểu diễn một lần dưới dạng cây trung lập (`FieldCondition` / `FilterGroup`) rồi dịch riêng cho từng backend, nên schema request tương thích OpenAI và ngôn ngữ truy vấn tách rời nhau.
- `ensure_collection` được tách khỏi `insert_documents` một cách có chủ đích. Gộp việc tạo vào đường insert buộc mọi batch song song tranh nhau một check-then-act — đó chính là lý do code cũ phải chạy batch đầu tiên một mình. Tạo một lần từ đầu khiến mọi insert đều thuần, nên chúng chạy song song được.
- Riêng với Qdrant: hỗ trợ sẵn sparse vector / BM25 trên cùng một collection (chỗ móc cho hybrid search sau này), cô lập tự nhiên theo collection đúng với mô hình dữ liệu, client async chính thức khớp với stack async hoàn toàn, cùng quantization và lưu trữ on-disk có sẵn.

**Nhược điểm**
- Thêm một service phải chạy trong stack Docker Compose, bên cạnh Postgres/MinIO/Redis.
- Lớp trừu tượng hiện chỉ được kiểm chứng bởi đúng một backend hoạt động, nên interface có thể chưa trung lập như kỳ vọng cho tới khi backend thứ hai thực sự được triển khai.
- Sparse/BM25 vẫn chỉ là chỗ móc trong wrapper Qdrant — lợi ích hybrid search còn là tiềm năng, chưa hiện thực.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| pgvector (extension của Postgres) | Không hỗ trợ BM25/sparse vector gốc; phải gắn thêm full-text search riêng, mất lợi thế "một collection cho cả dense lẫn sparse" |
| Elasticsearch / OpenSearch | Mạnh về BM25/full-text nhưng tối ưu kém hơn Qdrant cho vector similarity search thuần, và vận hành nặng hơn cho một service chỉ cần vector store |
| Gắn thẳng vào client Qdrant | Rẻ hơn ở hiện tại, nhưng lựa chọn backend sẽ rò rỉ vào service và pipeline, khiến việc migrate sau này thành viết lại chứ không phải thêm một provider |

## Vì sao `SearchType` đặt tên cho toàn bộ hình dạng retrieval

Retrieval được chọn bằng một giá trị `SearchType` duy nhất, rồi factory phân giải nó thành `_RetrievalPlan(retrievers, fusion)` — thay vì để caller truyền riêng danh sách retriever và chiến lược fusion.

Hai thứ đó luôn phải khớp nhau: nhiều retriever đi cùng `PassthroughFusion` sẽ âm thầm vứt đi một nửa kết quả. Đặt tên cho tổ hợp khiến các trạng thái sai không biểu diễn được, và `PassthroughFusion` sẽ raise chứ không cắt bớt nếu bị đưa nhiều hơn một danh sách candidate. Search type là tham số theo từng lời gọi chứ không phải cấu hình, vì hai query trên cùng một vector store hoàn toàn có thể muốn kiểu retrieval khác nhau.

Cái giá là thêm hybrid search phải động vào enum và factory thay vì chỉ truyền tham số khác — một sự đánh đổi có chủ đích: bớt linh hoạt cho caller để đổi lấy một bất biến không thể vô tình phá vỡ.

## Giới hạn đã biết

- **Chỉ ingest một file.** Nhiều hơn một `file_id` bị từ chối với lỗi 400 ngay tại request, và `IngestionService` kiểm tra lại rồi đánh dấu store `failed` thay vì báo `completed` trên một store rỗng.
- **Nhánh dự phòng `"fuse"`.** Gửi tường minh `{"type": "auto"}` (thay vì bỏ trống `chunking_strategy`) sẽ rơi vào strategy `"fuse"` mà `IngestionService` bỏ qua — store báo `completed` trong khi chưa có gì được index. Bỏ trống field này thì đi đúng nhánh `"auto"`.
- **`ranking_options` chưa có tác dụng.** Được schema chấp nhận, nhưng score threshold và quantization rescore chưa được phơi bày trên contract của vector store. `filters` thì **đã** được áp dụng.
- **Query dạng list bị cắt.** Nếu `query` là một list, chỉ phần tử đầu tiên được dùng.
- **Auth single-tenant.** Một `FASTAPI_API_KEY` dùng chung, dù các row đã được scope theo `api_key` như thể đã multi-tenant.
- **Object mồ côi.** Nếu insert Postgres thất bại sau khi upload MinIO thành công, object bị bỏ lại — chỉ được log, không có cơ chế dọn dẹp bù trừ.
- **Chỉ retrieval dense.** `SearchType.DENSE` là giá trị duy nhất được triển khai; seam `BaseRetriever` / `BaseFusion` đã có nhưng chưa có implementation keyword/BM25.
- **Milvus là placeholder.** Đã nối dây qua config, `VectorStoreType`, `VectorStoreFactory` và startup, nhưng mọi method đều raise `NotImplementedError`.
- **Upload chấp nhận nhiều hơn ingestion parse được.** `.docx`, `.csv`, `.json` và ảnh vượt qua validation lúc upload nhưng không có provider parsing nào đăng ký.
