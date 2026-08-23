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

Ingest một file (download → parse → persist_text → chunk → embed_index) có thể mất từ vài giây tới vài chục giây, đặc biệt khi parsing đi qua service ngoài (LlamaParse, Unstructured API) hoặc file lớn — quá lâu để giữ một HTTP request mở đồng bộ. `POST /v1/vector_stores` chỉ tạo record với `status=in_progress` và đẩy job; phần việc thật chạy trên một tiến trình worker riêng (TaskIQ, broker Redis Streams), tách khỏi vòng đời request.

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
- Nhiều lớp gián tiếp hơn khi đọc: theo dõi một lượt ingestion từ đầu tới cuối nghĩa là mở một factory, một context và bốn file stage thay vì một hàm duy nhất.
- Context mutable dùng chung là contract yếu hơn tham số tường minh — một stage về mặt kỹ thuật có thể đọc field mà stage trước chưa ghi, và chỉ fail lúc runtime.
- Chi phí này không đáng với một pipeline chỉ có hai bước; nó đáng ở đây vì cả hai pipeline đều được dự kiến sẽ mở rộng.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| `load_and_chunk_file()` / `embed_and_upload_chunks()` thủ tục (hình dạng cũ) | Mỗi bước mới đều phải sửa một hàm sẵn có, và mỗi bước phải tự nhớ mở span; hình dạng trace dần lệch khỏi hình dạng code |
| Framework điều phối bên thứ ba (Prefect, Dagster, LangChain chains) | Nặng hơn nhiều so với nhu cầu của một pipeline bốn bước in-process, và nó sẽ chiếm quyền tích hợp tracing mà repo này muốn trỏ thẳng vào Langfuse |

## Vì sao mọi năng lực đều nằm sau một interface provider

Parsing, chunking, embedding và vector database đều theo cùng một khuôn: một interface `base.py`, một thư mục `provider/` chứa các implementation, và một facade dựng ra một trong số đó. Với parsing, embedding và vector database, `from_settings()` của facade đọc một biến môi trường, và tên được validate trong `settings.py` nên gõ sai sẽ fail lúc startup chứ không phải lúc dùng lần đầu. Chunking là ngoại lệ: nó không có biến cấu hình nào, và `ChunkingService.for_strategy()` tra splitter qua `registry.py` — một hàm toàn phần trên enum strategy, nên không còn tổ hợp sai nào để phải validate.

**Ưu điểm**
- Đổi backend là sửa `.env`, không phải sửa code — hữu ích khi so sánh các chunker hoặc chuyển giữa embedding server tương thích OpenAI và TEI.
- Phần còn lại của codebase chỉ nhìn thấy interface, nên việc đổi backend không thể rò rỉ lên trên. `app/db/vector_store/types.py` bị cấm tường minh việc import SDK nhà cung cấp chính vì lý do này.
- Backend chưa cài không tốn gì: `VectorStoreFactory` định địa chỉ backend bằng đường dẫn module và import lúc dùng lần đầu, nên thiếu `pymilvus` chỉ thành vấn đề nếu Milvus thực sự được yêu cầu — và điều đó giữ đồ thị import không có chu trình.
- Một backend mới có thể được nối dây đầy đủ (config, enum, factory, startup) và merge trước cả khi nó chạy được, và backend Milvus đã về đích đúng theo cách đó: điền thân hàm mà không đổi gì phía trên `app.db`. Vì factory đánh khoá connection theo provider còn mỗi row vector store đều ghi provider của chính nó, có thể kết nối nhiều backend cùng lúc — việc chuyển đổi diễn ra từ từ chứ không phải cắt một nhát.

**Nhược điểm**
- Vấn đề mẫu số chung nhỏ nhất: interface chỉ phơi bày được những gì mọi backend đều làm được, nên tính năng riêng của backend (sparse vector của Qdrant, quantization rescore) cần hoặc mở rộng contract hoặc một lối thoát riêng.
- Nhiều file hơn cho mỗi năng lực so với việc gọi thẳng, và thêm một lớp gián tiếp khi debug.
- Config có thể để lại một tập backend được kết nối không phủ hết dữ liệu: credential kết nối được kiểm tra lúc startup, nhưng không có gì đối chiếu chúng với những provider mà các row đang tồn tại tham chiếu tới. Xoá credential của một backend vẫn còn giữ store thì sai sót đó chỉ lộ ra dưới dạng `RuntimeError` ở lần search đầu tiên vào một store như vậy, chứ không phải lúc boot.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Gọi thẳng SDK của từng nhà cung cấp tại nơi cần | Lựa chọn backend rò rỉ vào service và pipeline; đổi backend là phải sửa mọi call site |
| Một khối `if provider == ...` bên trong mỗi service | Ổn với hai provider và mục ruỗng ở bốn; đồng thời đặt import của mọi backend lên đường chạy nóng dù có dùng hay không |

## Vì sao model embedding được phục vụ bởi một deployment riêng

Mọi embedding đều là một lời gọi HTTP tới model server mà repo này không chạy. `EmbeddingService` ([`nlp4everyone/EmbeddingService`](https://github.com/nlp4everyone/EmbeddingService)) chính là server đó — vLLM phục vụ `Qwen/Qwen3-Embedding-0.6B` (dense) và `BAAI/bge-m3` (sparse) sau một API tương thích OpenAI — và nó là một repo riêng, một stack Compose riêng, không phải một service trong `compose_*.yml`.

**Ưu điểm**
- Image web/worker giữ nguyên dạng CPU-only: không layer CUDA, không trọng số model, không cần GPU để chạy test hay chạy API. Máy GPU và máy API có thể là hai máy khác nhau, hoặc hai nhóm scale khác nhau — model server thì đắt và dùng chung, còn replica API stateless thì không.
- Vòng đời model tách khỏi vòng đời ứng dụng. Đổi model embedding, chỉnh tỉ lệ VRAM hay restart vLLM đều không phải deploy lại service này; `check_connection()` lúc startup là điểm ràng buộc duy nhất, và nó cache số chiều vector từ chính cái đang thực sự trả lời.
- Hợp đồng ở đây là một API chứ không phải một thư viện, nên bất cứ thứ gì nói được OpenAI `/v1/embeddings` hoặc TEI đều thay thế được — một dịch vụ hosted, một endpoint cụm dùng chung, hay server của người khác. `EmbeddingService` là lựa chọn mặc định đã kiểm chứng, không phải một dependency.
- Nó giữ cho hai nửa độc lập mà vẫn hữu ích: repo kia phục vụ mọi consumer, repo này tiêu thụ mọi server.

**Nhược điểm**
- Phải clone hai repo và giữ hai file `.env` khớp nhau trước khi lần ingest đầu tiên chạy được — cặp port/model/API key được ghi rõ ở [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) chính vì không có gì tự ép chúng phải khớp.
- Chi phí embedding giờ có thêm một network hop cho mỗi batch, và một startup trước kia fail vì thiếu thư viện thì nay fail vì host không tới được.
- Lệch số chiều (server đổi model, collection thì không) chỉ lộ ra lúc ingest chứ không phải lúc cấu hình.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Thêm vLLM thành một service trong stack Compose này | Bắt buộc ai chạy API cũng phải có GPU, buộc việc restart model dính vào deploy ứng dụng, và làm mô hình phổ biến "một model server dùng chung, nhiều consumer" trở nên bất khả thi |
| Nạp model embedding ngay trong tiến trình (`sentence-transformers`) | Đưa trọng số model và CUDA vào image worker, khiến profile bộ nhớ của worker phụ thuộc model thay vì phụ thuộc batch, và mỗi replica API phải trả giá cho một bản copy riêng |

## Vì sao vector store không phụ thuộc provider (Qdrant và Milvus)

Mỗi vector store trong repo ứng với một collection riêng, truy cập qua `BaseAsyncVectorStore` chứ không phải qua client Qdrant. Provider được ghi **trên chính row của vector store**, không đọc từ config lúc truy vấn — và vì mọi backend đã điền credential đều được kết nối, nhiều engine cùng được phục vụ trong một tiến trình.

**Ưu điểm**
- Collection cũ vẫn chạy sau khi `VECTOR_STORE_PROVIDER` đổi: `get_store()` nhận provider mà store đó được tạo cùng, nên đổi mặc định chỉ ảnh hưởng store mới — miễn là credential của backend cũ vẫn còn đó, và đó chính là cách một cuộc chuyển đổi diễn ra từ từ thay vì cắt một nhát.
- Filter được biểu diễn một lần dưới dạng cây trung lập (`FieldCondition` / `FilterGroup`) rồi dịch riêng cho từng backend, nên schema request tương thích OpenAI và ngôn ngữ truy vấn tách rời nhau.
- `ensure_collection` được tách khỏi `insert_documents` một cách có chủ đích. Gộp việc tạo vào đường insert buộc mọi batch song song tranh nhau một check-then-act — đó chính là lý do code cũ phải chạy batch đầu tiên một mình. Tạo một lần từ đầu khiến mọi insert đều thuần, nên chúng chạy song song được.
- Riêng với Qdrant: sparse vector nằm chung collection với dense, và nó trộn cả hai nhánh ngay phía server (`prefetch` + `FusionQuery(RRF)`), nên hybrid search chỉ tốn một round-trip và không phải trộn trong process; thêm nữa là cô lập tự nhiên theo collection đúng với mô hình dữ liệu, client async chính thức khớp với stack async hoàn toàn, cùng quantization và lưu trữ on-disk có sẵn.
- Milvus đạt tới cùng contract đó bằng một con đường khác — `hybrid_search` với `RRFRanker`, một request mang mọi query vector thay vì kiểu fan-out song song của Qdrant — và đó chính là bằng chứng interface thật sự trung lập: triển khai nó không đổi bất cứ thứ gì phía trên `app.db`.

**Nhược điểm**
- Lớp trừu tượng đã được kiểm chứng bởi hai backend, đủ để cái giá "mẫu số chung nhỏ nhất" lộ ra cụ thể: Qdrant đặt tên trường vector theo model id còn Milvus từ chối và dùng tên cố định, Milvus vẫn phải gấp tên collection `vs-…` cũ thành `vs_…` vì không cho phép dấu gạch ngang, và nó chỉ phục vụ collection đã load trong khi Qdrant không có trạng thái đó. Mỗi khác biệt đều được hấp thụ bên trong provider của nó, nên bề mặt vẫn sạch, đổi lại code provider không đối xứng nhau.
- Mỗi backend được kết nối là thêm một dịch vụ ngoài phải chạy, giám sát và backup — và vì chúng nằm ngoài stack Compose, không gì trong `make up` báo cho bạn biết thiếu một cái cho tới khi bước probe lúc boot thất bại.
- Đẩy fusion xuống backend đồng nghĩa seam `BaseFusion` không được dùng bởi chính trường hợp nó sinh ra để phục vụ; một backend không trộn được ở server sẽ phải mang chiến lược fusion trở lại vào process.
- Ngữ nghĩa điểm số không đi qua lớp trừu tượng một cách nguyên vẹn: `score_threshold` là ngưỡng cosine trên Qdrant nhưng là `radius` trên Milvus, và với hybrid thì trên cả hai engine nó chỉ áp được cho nhánh dense. Contract thì giống nhau, con số phía sau thì không hẳn.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| pgvector (extension của Postgres) | Không hỗ trợ BM25/sparse vector gốc; phải gắn thêm full-text search riêng, mất lợi thế "một collection cho cả dense lẫn sparse" |
| Elasticsearch / OpenSearch | Mạnh về BM25/full-text nhưng tối ưu kém hơn Qdrant cho vector similarity search thuần, và vận hành nặng hơn cho một service chỉ cần vector store |
| Gắn thẳng vào client Qdrant | Rẻ hơn ở hiện tại, nhưng lựa chọn backend sẽ rò rỉ vào service và pipeline, khiến việc migrate sau này thành viết lại chứ không phải thêm một provider |
| Mỗi lúc chỉ một backend — đọc `VECTOR_STORE_PROVIDER` ngay lúc truy vấn | Biến việc đổi backend thành một nhát cắt: mọi store cũ trở nên không đọc được ngay khi biến đó đổi giá trị, nên đường migrate duy nhất là ingest lại toàn bộ trước khi chuyển. Ghi provider theo từng row và kết nối nhiều backend cùng lúc chỉ tốn một cột cùng một dict connection, đổi lại nhát cắt đó thành quá trình rút dần |

## Vì sao `SearchType` đặt tên cho toàn bộ hình dạng retrieval

Retrieval được chọn bằng một giá trị `SearchType` duy nhất, rồi factory phân giải nó thành `_RetrievalPlan(retrievers, fusion)` — thay vì để caller truyền riêng danh sách retriever và chiến lược fusion.

Hai thứ đó luôn phải khớp nhau: nhiều retriever đi cùng `PassthroughFusion` sẽ âm thầm vứt đi một nửa kết quả. Đặt tên cho tổ hợp khiến các trạng thái sai không biểu diễn được, và `PassthroughFusion` sẽ raise chứ không cắt bớt nếu bị đưa nhiều hơn một danh sách candidate. Search type là tham số theo từng lời gọi chứ không phải cấu hình, vì hai query trên cùng một vector store hoàn toàn có thể muốn kiểu retrieval khác nhau.

Hybrid sau đó vào đúng như hình dạng này dự đoán: thêm một giá trị enum và một nhánh trong `_build_plan()`. API phơi nó ra dưới dạng `search_type: "auto" | "dense" | "hybrid"`, mặc định là `"auto"` — tức là để `resolve_search_type()` trả lời theo từng search dựa trên thứ collection đang có, vì đầu vào trung thực cho quyết định đó là schema của collection chứ không phải mong muốn của caller.

Chỉ định thẳng `"hybrid"` thì được KIỂM TRA chứ không được thử: `hybrid_unavailable_reason()` được hỏi trước, và trả về 400 nêu rõ đang thiếu nửa nào. Âm thầm rơi về dense còn tệ hơn từ chối — caller đã đòi hybrid mà nhận kết quả dense sẽ đang đo chất lượng retrieval trên một cấu hình họ không nghĩ là mình đang chạy, và điều đó khó nhận ra hơn nhiều so với một request bị từ chối. Đúng một hàm đó trả lời cả câu "cái gì nên chạy" lẫn "vì sao không chạy được", nên kết quả phân giải và thông báo lỗi không thể lệch nhau.

Cái giá là thêm một search type phải động vào enum và factory thay vì chỉ truyền tham số khác — một sự đánh đổi có chủ đích: bớt linh hoạt cho caller để đổi lấy một bất biến không thể vô tình phá vỡ.

## Vì sao embed và index là một stage streaming, không phải hai stage tuần tự

Trước đây `EmbedStage` embed toàn bộ chunk của file rồi để `IndexStage` ghi tất cả lên vector store. Giờ chỉ còn `EmbedAndIndexStage`: mỗi batch được embed rồi upsert ngay trong cùng một lượt giữ semaphore, và `Document` chỉ được dựng sau khi lấy được semaphore.

**Ưu điểm**
- Bộ nhớ đỉnh có trần thật: `batch_size × concurrency` batch chunk/vector/Document, không phụ thuộc kích thước file. Hình dạng cũ giữ vector của *toàn bộ* file trong `context.embeddings` trước khi ghi được byte đầu tiên.
- Ghi bắt đầu sớm hơn: batch đầu tiên đã vào vector store trong khi các batch sau còn đang embed, nên tổng thời gian ingest gần với `max(embed, index)` hơn là `embed + index`.
- Vẫn đúng một lời gọi `ensure_collection()` từ đầu, nên không có race check-then-act trên đường insert.

**Nhược điểm**
- Không còn quan sát được embed và index như hai observation riêng trên Langfuse; hai con số `embed_wall_clock_s` / `index_wall_clock_s` trong cùng một span thay thế cho việc đó, và phải tính bằng `_union_duration()` vì các batch chạy chồng nhau.
- Stage gộp làm hai việc, nên nó không còn khớp với `ObservationType` nào: nó báo cáo dưới `SPAN` chứ không phải `EMBEDDING`.
- Ghi một phần khi lỗi: nếu một batch fail giữa đường, các batch trước đó **đã** nằm trong collection, trong khi store bị đánh dấu `failed`. Hình dạng cũ cũng có vấn đề này, nhưng ở đây nó xảy ra sớm hơn.
- Đổi lấy một điều kiện tiên quyết: phải biết `embedding_dim` *trước* khi embed, nên `EmbeddingService.check_connection()` buộc phải cache số chiều lúc startup và `get_dense_embedding_dim()` raise nếu bị gọi trước init.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Giữ hai stage, chỉ giảm `batch_size` | Không giải quyết được gốc: `context.embeddings` vẫn giữ vector của cả file, trần bộ nhớ vẫn theo kích thước file |
| Suy `embedding_dim` từ batch embed đầu tiên rồi mới tạo collection | Buộc lần ghi đầu phải chờ lần embed đầu, và đưa việc tạo collection trở lại đường insert — đúng cái race đã bỏ |

## Vì sao tách pool thread I/O khỏi pool CPU

Worker chạy hai `ThreadPoolExecutor` riêng — `IO_THREAD_POOL_SIZE=32` cho transfer MinIO, `CPU_THREAD_POOL_SIZE=4` cho chunking và cho việc băm sha256 phần byte vừa tải — cộng một `asyncio.Semaphore(STORAGE_CONCURRENCY=8)` chặn số thao tác MinIO song song.

**Ưu điểm**
- Hai loại công việc có hình dạng tối ưu trái ngược nhau: I/O muốn nhiều thread đang chờ mạng, CPU-bound thì oversubscribe chỉ thêm context switch. Một pool duy nhất buộc phải chọn sai cho một trong hai.
- Không bỏ đói lẫn nhau: một loạt transfer chậm không thể chiếm hết slot khiến chunking phải xếp hàng, và ngược lại.
- Trần storage là lớp bảo vệ thứ hai ở mức job: nhiều job ingestion đồng thời trong cùng tiến trình worker không thể tự làm cạn pool I/O. Nó phủ mọi lời gọi MinIO của pipeline — download, tra cache parse, ghi artifact — vì chặn một nửa thì consumer còn lại vẫn đủ sức làm cạn pool.

**Nhược điểm**
- Ba con số phải điều chỉnh thay vì một, và điều chỉnh đúng phụ thuộc vào phần cứng cùng kích thước file thực tế — vì thế cả ba đều nằm trong `config/config.yaml`.
- Thêm resource phải khởi tạo lúc startup, và một getter gọi trước init sẽ fail với `NameError` — đó chính là lý do provider chunking gọi `get_cpu_executor()` chứ không tự giữ pool riêng.
- Tiến trình web và worker không còn khởi tạo cùng một tập service (web bỏ pool CPU và semaphore storage), nên "cả hai chạy chung `app/startup.py`" giờ đúng ở mức từng hàm, không phải ở mức toàn bộ chuỗi.

## Vì sao lưu lại bản parse, và vì sao cache của nó bị giới hạn theo tài khoản

`PersistTextStage` ghi bản Markdown mà `ParseStage` tạo ra vào một bucket riêng, đánh khoá bằng SHA-256 nội dung file: `parsed/{api_key}/{provider}/{CACHE_VERSION}/{sha256}.md`. `ParseStage` tra khoá đó trước khi gọi vendor, nên cùng một chuỗi byte không bao giờ bị parse hai lần bởi cùng một backend cho cùng một tài khoản.

Parse là stage duy nhất mà kết quả không thể tái tạo miễn phí. Nó tính tiền theo trang, bị nhà cung cấp giới hạn tần suất, và model phía sau có thể đổi mà không báo trước — trong khi download, chunk và embed đều chạy lại thoải mái.

**Ưu điểm**
- Truy vết được: câu hỏi "vì sao store này trả ra kết quả đó?" trở thành việc mở một file ra đọc, thay vì upload lại rồi parse lại để đoán.
- Ingest lại một tài liệu với cấu hình chunking khác không tốn gì ở phía parse — đó chính là thứ khiến việc tinh chỉnh `chunk_size`/`chunk_overlap` trở nên khả thi.
- Cache đánh trúng trần thật của pipeline. Khi tải cao, ràng buộc siết trước là rate limit của vendor parse, không phải CPU hay RAM của deployment này.
- Việc ghi không bao giờ làm hỏng một lượt ingest: vector store vốn đã đúng kể cả khi thiếu artifact, nên mọi lỗi ở stage đó chỉ được log rồi nuốt.

**Nhược điểm**
- Thêm một bản sao đầy đủ toàn văn mọi tài liệu, nhân đôi bề mặt mà chính sách lưu trữ hay PII phải phủ.
- Không có gì xoá nó. `delete_file` cố ý không xoá, vì một artifact có thể phục vụ nhiều file, nên muốn chặn phình phải dùng lifecycle rule của bucket chứ không phải code ứng dụng.
- Tỷ lệ hit thấp hơn cảm giác ban đầu: SHA-256 chỉ trùng khi giống nhau từng byte, mà cùng một tài liệu export hai lần thì hiếm khi như vậy. Trường hợp hit đáng tin là chính tài khoản đó upload lại đúng file đó.

**Các phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Một cache toàn cục, không scope theo `api_key` | Rò rỉ qua thời gian phản hồi — hit trả về trong vài mili giây còn miss mất đúng bằng thời gian gọi API parse, đủ để bất kỳ ai kiểm chứng xem một tài liệu cụ thể đã từng được người khác upload hay chưa. Nó cũng khiến việc xoá sạch dữ liệu của một tài khoản là bất khả thi nếu không đếm tham chiếu |
| Chỉ đánh khoá theo byte của file, bỏ phần provider | Kết quả parse là hàm của parser chẳng kém gì hàm của file; đổi `PDF_PARSER_PROVIDER` xong cache vẫn âm thầm trả về output của backend cũ. `CACHE_VERSION` phủ nốt phần mà tên provider không nói lên được — options của parser, hay một lần nâng model phía vendor |
| Lưu text vào Postgres cạnh row của file | Text lớn làm phình row và phình mọi bản backup của chúng; object storage mới là thứ sinh ra cho lifecycle rule và đọc khối lượng lớn với giá rẻ |
| Để trong span Langfuse | Langfuse có giới hạn kích thước payload và retention riêng, và nó là hệ thống observability chứ không phải kho mà pipeline đọc ngược lại được |

## Vì sao mọi định dạng không phải PDF đều đi qua Unstructured API

`.txt`, `.md`, `.docx`, `.doc` và ảnh không còn dùng decoder in-process, mà đi qua Unstructured API; `.pdf` vẫn thuộc `PDF_PARSER_PROVIDER`.

**Ưu điểm**
- Output đồng nhất: mọi provider parsing giờ đều trả Markdown, nên chunking phía sau không phải phân biệt định dạng nguồn — quan trọng cho các splitter nhận biết cấu trúc (MarkdownHeader chẳng hạn).
- Mở rộng phạm vi định dạng mà không thêm code: `.docx`/`.doc` và ảnh (OCR) hoạt động ngay, thay vì mỗi định dạng một decoder.
- Cấu trúc tài liệu được giữ lại: heading theo `category_depth`, danh sách, và bảng dựng từ metadata `text_as_html` — thứ mà decoder text thuần không thể có.

**Nhược điểm**
- Một file `.txt` giờ cũng cần một lời gọi mạng và một API key, trong khi trước đó chỉ là `bytes.decode()`. Ingest chậm hơn và phụ thuộc thêm một service ngoài.
- Unstructured API không có output Markdown gốc (chỉ `application/json` hoặc `text/csv`), nên việc render Markdown là code của repo này và phải tự bám theo tập `category` của element.
- Nâng ràng buộc Python lên `>=3.11,<3.14` để khớp `unstructured==0.24.1`.

**Phương án đã cân nhắc**

| Phương án | Vì sao không chọn |
|---|---|
| Giữ decoder in-process cho `.txt`/`.md` | Hai đường parsing với hai hình dạng output khác nhau (text thuần vs Markdown), khiến chunking phải phân biệt định dạng nguồn |
| Chạy `unstructured` local (`partition` thay vì `partition_via_api`) | Kéo theo bộ dependency ML nặng và công việc CPU/OCR vào chính tiến trình worker, đúng thứ đang cố giữ ngoài đường chạy nóng |

## Giới hạn đã biết

- **Chỉ ingest một file.** Nhiều hơn một `file_id` bị từ chối với lỗi 400 ngay tại request, và `IngestionService` kiểm tra lại rồi đánh dấu store `failed` thay vì báo `completed` trên một store rỗng. Rào cản nằm ở schema chứ không ở lệnh kiểm tra: không có bảng `vector_store_files`, nên tiến độ của một store chỉ là một cột `status` — thứ không thể diễn tả "3 file xong, 1 file lỗi". Muốn ingest nhiều file thì phải mô hình hoá trạng thái theo từng file trước, đúng như sub-resource `vector_store.files` của chính OpenAI.
- **`file_counts` là suy ra chứ không phải đếm.** `_calculate_file_counts` ánh xạ đúng một cột status của store thành `completed=1` / `failed=1`; không có giá trị nào khác là khả dĩ. Field này tồn tại vì tương thích OpenAI và là hệ quả trực tiếp của giới hạn phía trên.
- **Chưa có bộ test tự động nào.** `pytest` và `pytest-asyncio` đã nằm trong nhóm dependency dev, nhưng không có thư mục `tests/` và không có lấy một file test — `examples/file_upload_example.py` chạy trên một stack đang sống là kiểm tra end-to-end duy nhất. Kiến trúc phân tầng vốn được dựng để dễ test (stage nhận một context thuần, `IngestionService` không import TaskIQ, provider nằm sau interface) nhưng chưa có gì khai thác điều đó.
- **`max_chunk_size_tokens` được đếm theo ký tự, không phải token.** Mọi splitter ở đây đều đo bằng ký tự (`tokenizer="character"` phía Chonkie, còn các splitter LangChain vốn đã đếm ký tự), nên field mà OpenAI đặt tên theo token thực chất là ngân sách ký tự. Muốn sửa thì phải dùng tokenizer thật, mà điều đó làm đổi ranh giới chunk của mọi store đang có nên cần kế hoạch re-index riêng. Những phần **không còn** là hạn chế: `chunking_strategy` vẫn chỉ mang kích thước, nhưng splitter giờ được chọn tường minh (`chunking_splitter`) hoặc tự suy ra từ tài liệu; yêu cầu overlap cho splitter không hỗ trợ sẽ bị 422 thay vì bị bỏ âm thầm; và thứ thực sự chạy được báo ngược lại — trên span `chunk` và trong `chunking_strategy.resolved` của store.
- **`ranking_options` mới có tác dụng một phần.** `score_threshold` giờ đã đi tới backend (gắn vào nhánh dense — áp lên output RRF thì sẽ loại sạch mọi thứ), nhưng `ranker` và `rewrite_query` vẫn được nhận rồi bỏ qua, còn quantization rescore chưa được phơi bày trên contract của vector store. `filters` thì **đã** được áp dụng.
- **Query dạng list bị cắt.** Nếu `query` là một list, chỉ phần tử đầu tiên được dùng.
- **Auth single-tenant.** Một `FASTAPI_API_KEY` dùng chung, dù các row đã được scope theo `api_key` như thể đã multi-tenant.
- **Object mồ côi.** Nếu insert Postgres thất bại sau khi upload MinIO thành công, object bị bỏ lại — chỉ được log, không có cơ chế dọn dẹp bù trừ.
- **Hybrid retrieval phụ thuộc vào thời điểm store được ingest.** `resolve_search_type` chỉ trả `HYBRID` cho collection đã có sẵn sparse vector, mà không backend nào thêm được field vector vào collection đang sống — nên bật `SPARSE_EMBEDDING_ENABLED` xong thì mọi store cũ vẫn dense-only cho tới khi ingest lại. Caller giờ có thể biết được điều đó bằng cách hỏi: `search_type: "hybrid"` trên một store như vậy sẽ trả 400 kèm lý do. Nhưng vẫn chưa có cách *đọc* trực tiếp chế độ của một store — `VectorStoreObject` không có field nào cho biết nó có sparse vector hay không, nên muốn biết thì phải thử search.
- **Upload và parsing chưa khớp allow-list.** `.csv`, `.json` và `.gif` vượt qua validation lúc upload nhưng không có provider parsing nào đăng ký. Ngược lại, `.md` và `.doc` parse được nhưng không nằm trong `ALLOWED_EXTENSIONS`, và `validate_file_type` chặn theo extension nên không có đường lách — hai định dạng đó luôn bị 415 ngay ở bước upload.
- **Ghi một phần khi ingestion lỗi.** `EmbedAndIndexStage` upsert theo từng batch, nên một batch fail giữa đường vẫn để lại các batch trước trong collection dù store bị đánh dấu `failed`; không có cơ chế dọn dẹp.
