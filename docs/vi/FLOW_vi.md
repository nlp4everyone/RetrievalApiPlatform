# Luồng xử lý chi tiết

> Tài liệu này mô tả chính xác điều gì xảy ra ở từng bước của pipeline — tên hàm, tên biến, và logic của từng nhánh kiểm tra. Dành cho developer cần hiểu sâu hoặc đang debug hệ thống.
> Phạm vi: tạo file (upload), tạo vector store (ingestion bất đồng bộ), và search.

## Sơ đồ thành phần

```text
App Startup  (FastAPI lifespan, phần trên yield trong app/app.py → app/startup.py)
        ├── init_tracing()                OpenTelemetry TracerProvider → export OTLP tới Langfuse
        ├── init_embed_model()             EmbeddingService.from_settings() → check_connection()
        │                                  ↳ check_connection() embed thử một câu và CACHE số chiều
        │                                    vector vào embed_model.dimension (get_dense_embedding_dim())
        ├── init_sparse_embed_model()      bản sao opt-in của bước trên, phụ thuộc SPARSE_EMBEDDING_ENABLED
        │                                  false → ghi log, để sparse_embed_model = None, boot dense-only
        │                                  true  → SparseEmbeddingService.from_settings() → check_connection()
        │                                          server sparse không tới được thì FAIL NGAY LÚC BOOT, không
        │                                          phải tới lần search đầu tiên — cùng giao kèo với dense
        │                                  ↳ caller rẽ nhánh bằng is_sparse_embedding_enabled(); gọi
        │                                    get_sparse_embed_model() khi đang tắt sẽ raise RuntimeError
        ├── init_postgres() + wait_for_postgres()   tạo pool, thử lại 5 lần / 0.5s, rồi _create_table()
        ├── init_vector_store()            một connection cho mỗi backend đã điền credential →
        │                                  check_connection() → register_connection(provider, conn)
        │                                  VECTOR_STORE_PROVIDER luôn nằm trong tập đó, và nó không truy
        │                                  cập được là BOOT FAIL — store mới được tạo trên nó, tiến trình
        │                                  không với tới nó thì chẳng phục vụ được gì; backend CÒN LẠI chỉ
        │                                  bị bỏ qua kèm WARNING, vì một khối .env còn sót cũng đủ khiến
        │                                  nó được kết nối
        ├── init_minio()                   MinioService, tạo UPLOADED_FILE_BUCKET nếu chưa có
        └── init_io_executor()             ThreadPoolExecutor(IO_THREAD_POOL_SIZE=32, prefix "io")
                                           tiến trình web chỉ cần pool I/O — nó upload/delete trên MinIO
                                           nhưng không parse, không chunk

Worker Startup  (TaskIQ WORKER_STARTUP, app/tasks/broker.py::_initialize_services)
        ├── init_tracing() · init_postgres() · init_minio()
        ├── init_io_executor()             pool I/O dùng chung cho download MinIO
        ├── init_cpu_executor()            ThreadPoolExecutor(CPU_THREAD_POOL_SIZE=4, prefix "cpu")
        │                                  TÁCH RIÊNG khỏi pool I/O: chunking là CPU-bound, không được
        │                                  xếp hàng sau — hay bị bỏ đói bởi — một lượt transfer chậm
        ├── init_download_semaphore()      asyncio.Semaphore(DOWNLOAD_CONCURRENCY=4)
        │                                  chặn trần số file tải song song, để một loạt job ingestion
        │                                  không tự làm cạn pool I/O
        ├── init_vector_store() · init_embed_model() · init_sparse_embed_model()
        │                                  worker cũng embed, nên nó dựng đúng hai service như tiến trình
        │                                  web — sparse vẫn là no-op khi bị tắt
        │                                  ⓘ init_postgres() ở đây chỉ tạo pool: wait_for_postgres() và
        │                                    _create_table() chỉ chạy trên tiến trình web
        └── init_parsing_service()         ParsingService.from_settings() ← chỉ worker cần
                                           toàn bộ chuỗi trên chạy một lần, chốt bằng _initialized
```

Lưu ý rằng `init_embed_model()` (và `init_sparse_embed_model()` khi bật `SPARSE_EMBEDDING_ENABLED`) thăm dò một server *nằm ngoài* stack này, nên embedding server phải chạy sẵn, nếu không **cả hai tiến trình đều không boot được** — đây là chủ đích, vì một model server không tới được mà không phát hiện sớm thì sẽ chỉ lộ ra dưới dạng một lần ingest thất bại rất lâu sau đó. Hãy khởi động [EmbeddingService](https://github.com/nlp4everyone/EmbeddingService) (`make up dense`, hoặc `make up hybrid` nếu cần cả sparse) trước khi `make up` ở đây; xem [Embedding Server](README_vi.md#embedding-server).

```text
Client  (OpenAI SDK / HTTP client)
        │  multipart/form-data (upload) hoặc JSON
        ▼
FastAPI HTTP Gateway
        ├── RequestIDMiddleware      gán/tái dùng X-Request-Id → request_id_ctx (ContextVar) → echo lại ở response header
        ├── file_router              POST /v1/files
        └── vector_store_router      POST /v1/vector_stores, POST /v1/vector_stores/{id}/search

① POST /v1/files   (multipart: purpose, file, expires_after)
        │
        ├── validate_file  (app/api/dependencies.py, chạy TRƯỚC handler)
        │       validate_file_type()   content_type ∉ ALLOWED_MIME_TYPES, ext ∉ ALLOWED_EXTENSIONS,
        │                              hoặc MIME_TYPE_MAPPING[content_type] != ext  → 415
        │       validate_file_size()   (file.size / 1MB) > MAX_FILE_SIZE (100)?  → FileSizeLimitExceededException (413)
        ▼
    FileService.upload_file(file, api_key, purpose, expires_after_seconds)
        ├── generate_file_id()            → "file-{8 hex}"
        ├── object_path = "{api_key}/uploads/{uuid4().hex}_{filename}"
        ├── MinioFileStore.upload_file(minio_client, file.file, file_size, object_path,
        │                              UPLOADED_FILE_BUCKET, content_type, executor=get_io_executor())
        │       put_object chạy trên pool I/O riêng, không phải default executor của event loop
        │       lỗi → log + raise lại (chưa có gì để dọn dẹp ở bước này)
        └── PostgresFileStore.insert_file(id=file_id, api_key, bytes, purpose, created_at, expires_at,
                                          metadata={filename, minio_bucket, minio_path, etag})
                lỗi kết nối (asyncpg.PostgresError | socket.gaierror)?
                    → raise PostgresConnectionException (503)
                    → object đã upload lên MinIO bị bỏ lại, trở thành MỒ CÔI — chỉ được log,
                      KHÔNG có cơ chế dọn dẹp tự động (không có compensating transaction/rollback)
                thành công → trả FileObject (created_at/expires_at dạng Unix timestamp)

② POST /v1/vector_stores   {name, file_ids, chunking_strategy} → ingestion bất đồng bộ
        ▼
    VectorStoreService.create(request, api_key)
        ├── len(request.file_ids) > 1 ?  → UnsupportedMultipleFilesException (400)
        │       ⓘ chỉ hỗ trợ ingest một file, từ chối ngay thay vì để caller poll
        │         một store không bao giờ hoàn thành
        ├── generate_vectorstore_id()       → "vs_{32 hex}"
        ├── provider = VectorStoreFactory.default_provider()      ← từ VECTOR_STORE_PROVIDER
        │
        ├── traced_span("POST /v1/vector_stores")   ROOT span của trace ingestion
        │       các attribute mức trace (user_id, tags, input, metadata) BẮT BUỘC đặt ở đây,
        │       không phải trong worker — worker nhập vào trace này và Langfuse bỏ qua
        │       attribute mức trace đặt trên span không phải root
        │
        │       ├── traced_span("create_record")
        │       │       PostgresVectorStore.create(status=IN_PROGRESS, usage_bytes=0,
        │       │                                  vector_store_type=provider, ...)
        │       │
        │       ├── Phân giải chunking_strategy + chunk_size/chunk_overlap:
        │       │       request.chunking_strategy.type == "static"  → "static" (từ request.static)
        │       │       mọi trường hợp còn lại — bỏ trống field, hoặc gửi tường minh {"type": "auto"}
        │       │                                                   → "auto" (800 / 400)
        │       │
        │       └── traced_span("enqueue_ingestion")
        │               ingest_vector_store_files.kiq(vectorstore_id, api_key, file_ids,
        │                                             chunking_strategy, chunk_size, chunk_overlap,
        │                                             request_id=request_id_ctx.get(),
        │                                             trace_context=inject_trace_context(),   ← W3C traceparent
        │                                             vector_store_type=str(provider))
        │                                                    ↳ lên Redis stream broker (TaskIQ)
        │
        └── trả VectorStoreObject(status="in_progress")   ← trả về ngay, KHÔNG chờ ingestion

        Lưu ý: chạy trên tiến trình taskiq_worker riêng, ngoài vòng đời request/response của HTTP
        ▼
    app/tasks/ingestion_task.py::ingest_vector_store_files(...)
        │  request_id_ctx.set(request_id)   ← bind lại ContextVar bên trong tiến trình worker
        │  uỷ quyền cho IngestionService, log + raise lại khi lỗi, cuối cùng reset contextvar
        ▼
    IngestionService.ingest_vector_store_files(...)
        │
        ├──▶ Bước 0: chunking_strategy thuộc ("auto","static") ?
        │       KHÔNG → log lỗi → _mark_failed(status=FAILED) → raise ValueError
        │               (bên gửi và worker đã lệch nhau; fail còn hơn im lặng bỏ qua)
        │
        ├──▶ Bước 1: PostgresFileStore.check_existing_files(file_ids) → existing_file_ids
        │       có file_ids nhưng không tồn tại file nào? → log lỗi → _mark_failed → raise ValueError
        │       không có file_ids nào → usage_bytes giữ nguyên 0, bỏ qua Bước 2-3, nhảy tới Bước 4
        │                               (store tạo ra mà không kèm file thì rỗng là đúng)
        │       còn lại → get_total_bytes(existing_file_ids) → usage_bytes
        │                get_metadata_for_files(existing_file_ids) → files_metadata
        │
        ├──▶ Bước 2: len(files_metadata) == 1 ?   ← lớp phòng thủ thứ hai sau lần kiểm tra ở tầng API
        │       KHÔNG (0 hoặc >1) → log lỗi → _mark_failed(status=FAILED) → raise ValueError
        │              0 vẫn qua được Bước 1: get_metadata_for_files loại row có metadata
        │              là NULL, nên file tồn tại mà không có metadata sẽ rơi vào đây
        │
        ├──▶ Bước 3: _ingest_single_file(...) → num_inserted
        │               vector_store = VectorStoreFactory.get_store(collection_name=vectorstore_id,
        │                                                           provider=vector_store_type)
        │               pipeline = build_ingestion_pipeline(minio_client, vector_store, embed_fn=get_dense_embedding,
        │                                                   parsing_service, chunking_strategy, chunk_size, chunk_overlap,
        │                                                   sparse_embed_fn=get_sparse_embedding
        │                                                                   if is_sparse_embedding_enabled() else None)
        │                                                   ↳ worker chỉ đưa sparse embedder khi nó thực sự có một cái;
        │                                                     việc đưa vào chính là thứ làm collection thành hybrid
        │                                                     (dense + sparse trên cùng một point)
        │               context  = IngestionContext(vector_store_id, api_key, file_id, file_metadata, ...)
        │               await pipeline.run(context, parent_carrier=trace_context)
        │                                                   ↳ lồng mọi stage vào trace của HTTP request
        │
        │               ┌── Các stage của IngestionPipeline (mỗi stage một observation Langfuse) ──┐
        │               │                                                                          │
        │               │  download   MinioFileStore.download_file → context.raw_bytes             │
        │               │             bucket/path đọc từ context.file_metadata                     │
        │               │             async with get_download_semaphore()  ← DOWNLOAD_CONCURRENCY=4│
        │               │             _fetch_object() (get_object + .read() + close) chạy NGUYÊN   │
        │               │             KHỐI trên get_io_executor(): mở stream mới chỉ là header,    │
        │               │             transfer thật nằm ở .read() — cả hai phải rời event loop     │
        │               │             trả None → ValueError("Failed to download file: ...")        │
        │               │                                                                          │
        │               │  parse      ParsingService.parse(raw_bytes, context.file_extension)      │
        │               │             → context.text  (Markdown cho MỌI định dạng)                 │
        │               │             .pdf → PDF_PARSER_PROVIDER (LlamaParseProvider)              │
        │               │             .txt .md .docx .doc .png .jpg .jpeg → UnstructuredProvider   │
        │               │                 partition_via_api trên asyncio.to_thread, rồi element    │
        │               │                 list JSON được render thành Markdown tại chỗ             │
        │               │             extension không có trong map → ValueError("Unsupported ...") │
        │               │                                                                          │
        │               │  chunk      ChunkingService.split_text(text) → context.chunks            │
        │               │             provider từ CHUNKING_PROVIDER, size/overlap theo request     │
        │               │             chạy trên get_cpu_executor() — CPU-bound, không được đứng    │
        │               │             chung pool với I/O                                           │
        │               │                                                                          │
        │               │  embed_index  MỘT stage streaming (EmbedAndIndexStage)                   │
        │               │             ① embedding_dim = await get_dense_embedding_dim()            │
        │               │                (số chiều đã cache lúc startup — KHÔNG suy ra từ kết quả  │
        │               │                 embed đầu tiên, đó chính là điều cho phép streaming)     │
        │               │             ② ensure_collection(embedding_dim, with_sparse=…) MỘT LẦN    │
        │               │                with_sparse = có sparse_embed_fn được truyền vào hay không│
        │               │                → context.metrics["collection_created"]                   │
        │               │             ②ᵇ use_sparse = want_sparse and await supports_sparse()      │
        │               │                HỎI COLLECTION chứ không tin config: collection tạo trước │
        │               │                khi bật sparse thì không có field sparse, KHÔNG backend   │
        │               │                nào thêm được, và upsert sparse vào đó sẽ fail cả batch   │
        │               │                ⓘ ensure_collection trả False vì worker khác thắng cuộc   │
        │               │                  đua không phải lỗi trên cả hai backend — nó kiểm tra    │
        │               │                  lại sự tồn tại rồi đi tiếp                              │
        │               │                → context.metrics["sparse_enabled"]                       │
        │               │             ③ chunks → batch cỡ EMBEDDING_UPLOAD_BATCH_SIZE (16),        │
        │               │                asyncio.gather dưới Semaphore(BATCH_CONCURRENCY=4);       │
        │               │                mỗi batch: embed → dựng Document → insert_documents,      │
        │               │                TẤT CẢ bên trong cùng một lượt giữ semaphore              │
        │               │                use_sparse → dense và sparse được embed cùng lúc trong    │
        │               │                  một asyncio.gather, nên batch tốn thời gian của server  │
        │               │                  CHẬM HƠN chứ không phải tổng hai lượt, và cả hai vector │
        │               │                  cùng nằm trên một point (không cần quét file lần hai)   │
        │               │                → bộ nhớ đỉnh ≈ batch_size × concurrency, không phụ       │
        │               │                  thuộc kích thước file; ghi bắt đầu ngay từ batch đầu    │
        │               │                  chứ không chờ embed xong toàn file                      │
        │               │             ④ context.num_inserted = sum(...)                            │
        │               │                embed_wall_clock_s / index_wall_clock_s tính bằng         │
        │               │                _union_duration() — hợp các khoảng thời gian chồng nhau,  │
        │               │                nên chạy song song không bị đếm trùng                     │
        │               │             ⓘ stage này báo cáo dưới ObservationType.SPAN (mặc định),    │
        │               │               không phải EMBEDDING như EmbedStage trước khi gộp          │
        │               │                                                                          │
        │               └──────────────────────────────────────────────────────────────────────────┘
        │
        │               stage nào raise? → các stage còn lại không chạy → span ghi lại lỗi
        │                                → IngestionService bắt → _mark_failed(status=FAILED) → raise lại
        │
        │               num_inserted == 0? → file không cho ra chunk nào, collection rỗng
        │                                  → _mark_failed(status=FAILED) → raise ValueError
        │
        └──▶ Bước 4: PostgresVectorStore.update(status=COMPLETED, usage_bytes, last_active_at=now())
                     ← chỉ tới được đây khi collection thật sự search được, hoặc khi store
                       được tạo ra mà không kèm file_ids nào

③ POST /v1/vector_stores/{id}/search   {query, max_num_results, filters, ranking_options, search_type}
        ▼
    VectorStoreService.search(vector_store_id, search_request, api_key, search_type=None)
        ├── validate_vector_store_prefix(id)    không có prefix "vs"?  → WrongPrefixVectorstoreException
        ├── PostgresVectorStore.get_by_id(id, api_key)   không tìm thấy? → VectorStoreNotFoundException
        │       provider = record["vector_store_type"]   ← backend mà collection này thực sự nằm trên
        │
        ├── neutral_filter = normalize_search_filter(search_request.filters)
        │       ComparisonFilter → FieldCondition(key, FilterOperator, value)
        │       CompoundFilter   → FilterGroup(FilterCombinator, [...])
        │       mỗi backend tự render cây này (to_qdrant_filter / to_milvus_expression)
        │
        │  query = search_request.query nếu là str, ngược lại search_request.query[0]
        │      ⚠ nếu query là List[str], CHỈ phần tử đầu tiên được dùng — phần còn lại bị bỏ
        │
        ├── score_threshold = ranking_options.score_threshold  (chỉ khi > 0, ngược lại None)
        │       0.0 là giá trị mặc định của schema, nghĩa là "giữ tất cả", nên KHÔNG truyền xuống —
        │       backend hiểu None là "không lọc"
        │       ⓘ trong ranking_options, chỉ score_threshold đi tới được backend; ranker và
        │         rewrite_query vẫn được nhận rồi bỏ qua
        │
        ├── vector_store = VectorStoreFactory.get_store(collection_name=id, provider=provider)
        │       provider lấy từ ROW chứ không bao giờ từ config: store tạo trên backend mà deployment
        │       này không còn kết nối sẽ raise RuntimeError kèm cách sửa ("điền credential
        │       kết nối của nó vào") thay vì đi query nhầm engine
        │       Riêng Milvus: collection vật lý chính là id (vs_{hex} vốn đã hợp lệ ở đó), trừ id
        │       gạch ngang cũ vẫn phải gấp (vs-a1b2… → vs_a1b2…); một search gặp collection chưa
        │       load — như sau khi server khởi động lại — sẽ load rồi thử lại MỘT lần thay vì trả rỗng
        │
        ├── search nào thực sự chạy — phân giải TRƯỚC khi mở span, để trace ghi cái đã chạy
        │   chứ không phải cái được yêu cầu:
        │       1. THAM SỐ search_type (chỉ dành cho caller nội bộ) thắng tuyệt đối
        │       2. nếu không, search_request.requested_search_type — field "auto"|"dense"|"hybrid"
        │             của request, ánh xạ thành None|DENSE|HYBRID ("auto" là mặc định)
        │             ⓘ không thuộc schema OpenAI; SDK gốc gửi nó qua
        │               extra_body={"search_type": "hybrid"}
        │       3. vẫn None ("auto") → resolve_search_type(vector_store)
        │             hybrid_unavailable_reason(vector_store) là None → HYBRID, ngược lại DENSE
        │       4. HYBRID được chỉ định → hybrid_unavailable_reason(vector_store) được kiểm tra lại,
        │             và nếu có lý do thì raise UnsupportedSearchTypeException (400) thay vì âm thầm
        │             rơi về dense — caller đã gọi tên hybrid mà nhận kết quả dense sẽ đang đo chất
        │             lượng retrieval trên một cấu hình họ không nghĩ là mình đang chạy
        │                 "sparse embedding is not enabled on this server"              (phía server)
        │                 "this vector store holds no sparse vectors — ingested before …" (phía store)
        │             hai lý do vì cách sửa khác nhau: bật sparse, so với ingest lại
        │       (DENSE được chỉ định thì luôn chạy: store có hybrid vẫn search dense được)
        │
        ├── traced_span("POST /v1/vector_stores/{id}/search")   ROOT span
        │       attribute mức trace: user_id=api_key, tags=["vector_store_search"],
        │                            input={query, max_num_results}, metadata={vector_store_id,
        │                            vector_store_type, search_type}
        │
        │       pipeline     = build_retrieval_pipeline(vector_store, embed_fn=get_dense_embedding,
        │                                               search_type, sparse_embed_fn)
        │              └── _build_plan(search_type, vector_store):
        │                      SearchType.DENSE  → [DenseRetriever],  PassthroughFusion
        │                      SearchType.HYBRID → [HybridRetriever], PassthroughFusion
        │                      còn lại           → ValueError("Unsupported search type")
        │                  HYBRID mà không có sparse_embed_fn → ValueError
        │                      (không thể chạm tới từ API: bước 4 ở trên đã chặn bằng 400 trước rồi)
        │       context      = RetrievalContext(vector_store_id, api_key, query, limit=max_num_results,
        │                                       filters=neutral_filter, score_threshold=score_threshold)
        │       await pipeline.run(context)      ← không cần parent_carrier: đã nằm trong span của request
        │
        │       ┌── Các stage của RetrievalPipeline (mỗi stage một observation Langfuse) ─────────┐
        │       │                                                                                 │
        │       │  embed_query   embed_fn([query]) → context.dense_vector                         │
        │       │                HYBRID: + sparse_embed_fn([query]) → context.sparse_vector       │
        │       │                    (await cùng lúc — tốn thời gian của server chậm hơn)         │
        │       │                                            [ObservationType.EMBEDDING]          │
        │       │                                                                                 │
        │       │  retrieve      mọi retriever chạy song song (asyncio.gather), cùng nhận một     │
        │       │                RetrievalQuery (text, limit, cả hai vector, filters,             │
        │       │                score_threshold)                                                 │
        │       │                → context.candidates = {retriever.name: [RetrievedChunk]}        │
        │       │                Dense/HybridRetriever: collection_exists()? KHÔNG → []           │
        │       │                    (row của store có thể tồn tại trước khi ingestion tạo        │
        │       │                     collection — kết quả rỗng, không phải lỗi)                  │
        │       │                HybridRetriever: một retrieve() mang cả hai vector;              │
        │       │                    mỗi nhánh được prefetch sâu limit×N, để một document         │
        │       │                    nằm ngay ngoài top-k dense vẫn còn cơ hội thắng theo         │
        │       │                    rank, rồi backend trộn ngay ở server → MỘT danh sách         │
        │       │                        Qdrant: prefetch + FusionQuery(RRF), hoặc                │
        │       │                                RrfQuery(k) khi đặt retrieval.rrf_k              │
        │       │                        Milvus: hybrid_search(AnnSearchRequest×2, RRFRanker)     │
        │       │                    score_threshold CHỈ gắn vào nhánh dense — nó là điểm         │
        │       │                    similarity, đem so với output RRF (~1/(60+rank)) thì         │
        │       │                    sẽ loại sạch mọi thứ (Milvus nhận nó dưới dạng tham          │
        │       │                    số search `radius` chứ không phải ngưỡng điểm)               │
        │       │                    query không có sparse vector? → tự hạ xuống dense thay vì    │
        │       │                    fail; span ghi lại ở retrieval.hybrid.used_sparse            │
        │       │                                            [ObservationType.RETRIEVER]          │
        │       │                                                                                 │
        │       │  fuse          fusion.fuse(candidates, limit) → context.results                 │
        │       │                PassthroughFusion: >1 danh sách candidate → ValueError           │
        │       │                    (hybrid vẫn trả một danh sách — backend đã trộn rồi)         │
        │       │                emits_span() == False khi không có gì để trộn                    │
        │       │                                                                                 │
        │       └─────────────────────────────────────────────────────────────────────────────────┘
        │
        │       data = convert_retrieved_chunks_to_search_results(context.results)
        │              content được chuẩn hoá khoảng trắng, score làm tròn 5 chữ số THẬP PHÂN ở ĐÂY và
        │              chỉ ở đây — ngưỡng lọc phía trên và thứ hạng RRF đều dùng score đầy đủ
        │
        └── trả VectorStoreSearchResponse(search_query, data, has_more = len(data) >= max_num_results)

App Shutdown  (FastAPI lifespan, phần dưới yield trong app/app.py)
        ├── get_io_executor().shutdown(cancel_futures=True)   cắt luồng I/O trước, để không còn gì
        │                                        chạm tới MinIO hay các client bị đóng bên dưới
        ├── VectorStoreFactory.close_all()       đóng mọi connection vector store đã đăng ký
        ├── get_postgres_client().close()        đóng pool asyncpg
        ├── close_embed_model()                  đóng HTTP client của dense embedding provider
        └── close_sparse_embed_model()            đóng client sparse, nếu SPARSE_EMBEDDING_ENABLED
                                                 TracerProvider cố ý không đụng tới: nó tự đăng ký
                                                 atexit để flush, gọi shutdown ở đây sẽ cắt mất
                                                 chính các dòng log shutdown này

Worker Shutdown  (TaskIQ WORKER_SHUTDOWN, app/tasks/broker.py)
        ├── get_postgres_client().close()        đóng pool asyncpg — chỉ khi _initialized
        ├── close_embed_model()                  đóng HTTP client dense embedding — cùng điều kiện guard
        ├── close_sparse_embed_model()            đóng client sparse, nếu bật — cùng điều kiện guard
        └── VectorStoreFactory.close_all()       đóng mọi connection vector store đã đăng ký
                                                 (luôn chạy, kể cả khi startup đã fail)
```

## Tracing đến từ đâu

Stage không bao giờ mở span. `Pipeline.run()` mới mở, và nó là thứ duy nhất làm điều đó:

```text
traced_span(pipeline.name, root_attributes(context), parent_carrier)
    for stage in stages:
        traced = stage.emits_span(context)          ← quyết định TRƯỚC run(), xem được kết quả stage trước
        with traced_span(stage.name, {OBSERVATION_TYPE: stage.observation_type}) if traced else nullcontext():
            await stage.run(context)
            set_span_attributes(span, stage.span_attributes(context))   ← đọc SAU khi run() thành công
    set_span_attributes(pipeline_span, result_attributes(context))
```

Một stage báo `emits_span() == False` vẫn chạy — nó chỉ đứng ngoài trace, nên một bước không làm gì ở lần chạy cụ thể này không gây nhiễu. Đây là lý do hình dạng trace vẫn đúng khi stage được thêm, bớt, hay đảo thứ tự.
