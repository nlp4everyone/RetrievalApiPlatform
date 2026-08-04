# Luồng xử lý chi tiết

> Tài liệu này mô tả chính xác điều gì xảy ra ở từng bước của pipeline — tên hàm, tên biến, và logic của từng nhánh kiểm tra. Dành cho developer cần hiểu sâu hoặc đang debug hệ thống.
> Phạm vi: tạo file (upload), tạo vector store (ingestion bất đồng bộ), và search.

## Sơ đồ thành phần

```text
App Startup  (FastAPI "startup" event, app/app.py → app/startup.py)
        ├── init_tracing()                OpenTelemetry TracerProvider → export OTLP tới Langfuse
        ├── init_embed_model()             EmbeddingService.from_settings() → check_connection()
        │                                  ↳ check_connection() embed thử một câu và CACHE số chiều
        │                                    vector vào embed_model.dimension (get_dense_embedding_dim())
        ├── init_postgres() + wait_for_postgres()   tạo pool, thử lại 5 lần / 0.5s, rồi _create_table()
        ├── init_vector_store()            connection cho VECTOR_STORE_PROVIDER → check_connection()
        │                                  → VectorStoreFactory.register_connection(provider, conn)
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
        ├── init_vector_store() · init_embed_model()
        └── init_parsing_service()         ParsingService.from_settings() ← chỉ worker cần
                                           toàn bộ chuỗi trên chạy một lần, chốt bằng _initialized

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
        ├── generate_vectorstore_id()       → "vs-{32 hex}"
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
        │       │       request.chunking_strategy is None                     → "auto"   (800 / 400)
        │       │       request.chunking_strategy.type == "static"             → "static" (từ request.static)
        │       │       request.chunking_strategy.type == "auto" (gửi tường minh) → rơi vào else → "fuse" (800 / 400)
        │       │           ⚠ "fuse" không thuộc ("auto","static"), nên IngestionService BỎ QUA pipeline —
        │       │             store vẫn bị đánh dấu "completed" dù chưa có gì được index.
        │       │             Chỉ xảy ra khi client gửi tường minh {"type": "auto"}
        │       │             thay vì bỏ trống chunking_strategy.
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
        ├──▶ Bước 1: PostgresFileStore.check_existing_files(file_ids) → existing_file_ids
        │       rỗng?  → usage_bytes giữ nguyên 0, bỏ qua Bước 2-3, nhảy tới Bước 4 (status=COMPLETED)
        │       không → get_total_bytes(file_ids) → usage_bytes
        │               get_metadata_for_files(existing_file_ids) → files_metadata
        │
        ├──▶ Bước 2: len(files_metadata) == 1 ?   ← lớp phòng thủ thứ hai sau lần kiểm tra ở tầng API
        │       KHÔNG (0 hoặc >1) → log lỗi → _mark_failed(status=FAILED) → raise ValueError
        │                           (cố ý KHÔNG báo "completed" trên một store rỗng)
        │
        ├──▶ Bước 3: chunking_strategy thuộc ("auto","static") ?
        │       KHÔNG → bỏ qua toàn bộ pipeline (xem ⚠ "fuse" ở trên)
        │       CÓ    → _ingest_single_file(...)
        │               vector_store = VectorStoreFactory.get_store(collection_name=vectorstore_id,
        │                                                           provider=vector_store_type)
        │               pipeline = build_ingestion_pipeline(minio_client, vector_store, embed_fn=get_dense_embedding,
        │                                                   parsing_service, chunking_strategy, chunk_size, chunk_overlap)
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
        │               │             ② ensure_collection(embedding_dim) MỘT LẦN, từ đầu           │
        │               │                → context.metrics["collection_created"]                   │
        │               │             ③ chunks → batch cỡ EMBEDDING_UPLOAD_BATCH_SIZE (16),        │
        │               │                asyncio.gather dưới Semaphore(BATCH_CONCURRENCY=4);       │
        │               │                mỗi batch: embed → dựng Document → insert_documents,      │
        │               │                TẤT CẢ bên trong cùng một lượt giữ semaphore              │
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
        └──▶ Bước 4: PostgresVectorStore.update(status=COMPLETED, usage_bytes, last_active_at=now())

③ POST /v1/vector_stores/{id}/search   {query, max_num_results, filters, ranking_options}
        ▼
    VectorStoreService.search(vector_store_id, search_request, api_key, search_type=SearchType.DENSE)
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
        │  ⓘ TODO: ranking_options vẫn chưa có tác dụng — score_threshold và quantization rescore
        │     chưa được phơi bày trên contract của vector store
        │
        ├── traced_span("POST /v1/vector_stores/{id}/search")   ROOT span
        │       attribute mức trace: user_id=api_key, tags=["vector_store_search"],
        │                            input={query, max_num_results}, metadata={vector_store_id,
        │                            vector_store_type, search_type}
        │
        │       vector_store = VectorStoreFactory.get_store(collection_name=id, provider=provider)
        │       pipeline     = build_retrieval_pipeline(vector_store, embed_fn=get_dense_embedding, search_type)
        │              └── _build_plan(search_type):
        │                      SearchType.DENSE → [DenseRetriever], PassthroughFusion
        │                      còn lại          → ValueError("Unsupported search type")
        │       context      = RetrievalContext(vector_store_id, api_key, query, limit=max_num_results,
        │                                       filters=neutral_filter)
        │       await pipeline.run(context)      ← không cần parent_carrier: đã nằm trong span của request
        │
        │       ┌── Các stage của RetrievalPipeline (mỗi stage một observation Langfuse) ─────────┐
        │       │                                                                                 │
        │       │  embed_query   embed_fn([query]) → context.dense_vector                         │
        │       │                                            [ObservationType.EMBEDDING]          │
        │       │                                                                                 │
        │       │  retrieve      mọi retriever chạy song song (asyncio.gather)                    │
        │       │                → context.candidates = {retriever.name: [RetrievedChunk]}        │
        │       │                DenseRetriever: collection_exists()? KHÔNG → []                  │
        │       │                    (row của store có thể tồn tại trước khi ingestion tạo        │
        │       │                     collection — kết quả rỗng, không phải lỗi)                  │
        │       │                                            [ObservationType.RETRIEVER]          │
        │       │                                                                                 │
        │       │  fuse          fusion.fuse(candidates, limit) → context.results                 │
        │       │                PassthroughFusion: >1 danh sách candidate → ValueError           │
        │       │                    (thêm retriever buộc phải chọn fusion thật)                  │
        │       │                emits_span() == False khi không có gì để trộn                    │
        │       │                                                                                 │
        │       └─────────────────────────────────────────────────────────────────────────────────┘
        │
        │       data = convert_retrieved_chunks_to_search_results(context.results)
        │
        └── trả VectorStoreSearchResponse(search_query, data, has_more = len(data) >= max_num_results)

Worker Shutdown  (TaskIQ WORKER_SHUTDOWN, app/tasks/broker.py)
        ├── get_postgres_client().close()        đóng pool asyncpg
        └── VectorStoreFactory.close_all()       đóng mọi connection vector store đã đăng ký
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
