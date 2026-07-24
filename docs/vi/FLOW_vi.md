# Luồng xử lý chi tiết

> Tài liệu này mô tả chính xác từng bước trong pipeline — tên hàm, tên biến, và logic từng gate. Dành cho developer cần hiểu sâu hoặc debug hệ thống.
> Phạm vi: tạo file (upload), tạo vector store (ingest bất đồng bộ), và search.

## Sơ đồ component

```text
App Startup  (FastAPI "startup" event, app/app.py)
        ├── init_tracing()                OpenTelemetry TracerProvider → export OTLP tới Langfuse
        ├── init_embed_model()             AsyncOpenAI client → vLLM dense embedding, gọi thử embeddings.create(["Hello"])
        ├── init_postgres() + wait_for_postgres()   tạo pool, retry 5 lần / 0.5s, rồi _create_table()
        ├── init_qdrant()                  QdrantService._check_connection()
        └── init_minio()                   MinioService, tạo UPLOADED_FILE_BUCKET nếu chưa tồn tại

Client  (OpenAI SDK / HTTP client)
        │  multipart/form-data (upload) hoặc JSON
        ▼
FastAPI HTTP Gateway
        ├── RequestIDMiddleware      gán/tái sử dụng X-Request-Id → request_id_ctx (ContextVar) → echo lại response header
        ├── file_router              POST /v1/files
        └── vector_store_router      POST /v1/vector_stores, POST /v1/vector_stores/{id}/search

① POST /v1/files   (multipart: purpose, file, expires_after)
        │
        ├── validate_file  (FastAPI dependency, chạy TRƯỚC handler)
        │       validate_file_type()   content_type ∉ ALLOWED_MIME_TYPES, ext ∉ ALLOWED_EXTENSIONS,
        │                              hoặc MIME_TYPE_MAPPING[content_type] != ext  → 415
        │       validate_file_size()   (file.size / 1MB) > MAX_FILE_SIZE (100)?  → FileSizeLimitExceededException (413)
        ▼
    FileService.upload_file(file, api_key, purpose, expires_after_seconds)
        ├── generate_file_id()            → "file-{8 hex}"
        ├── object_path = "{api_key}/uploads/{uuid4().hex}_{filename}"
        ├── MinioFileStore.upload_file(minio_client, file.file, file_size, object_path, UPLOADED_FILE_BUCKET, content_type)
        │       lỗi → log + re-raise (chưa có gì để dọn ở bước này)
        └── PostgresFileStore.insert_file(id=file_id, api_key, bytes, purpose, created_at, expires_at,
                                          metadata={filename, minio_bucket, minio_path, etag})
                lỗi kết nối (asyncpg.PostgresError | socket.gaierror)?
                    → raise PostgresConnectionException (503)
                    → object đã upload thành công trên MinIO vẫn còn đó, trở thành ORPHAN — chỉ được log lại,
                      KHÔNG có cơ chế dọn tự động (không có compensating transaction/rollback)
                thành công → trả về FileObject (created_at/expires_at là Unix timestamp)

② POST /v1/vector_stores   {name, file_ids, chunking_strategy} → ingest bất đồng bộ
        ▼
    VectorStoreService.create(request, api_key)
        ├── generate_vectorstore_id()       → "vs-{32 hex}"
        ├── PostgresVectorStore.create(status=UploadingStatus.IN_PROGRESS, usage_bytes=0, ...)
        ├── Xác định chunking_strategy + chunk_size/chunk_overlap:
        │       request.chunking_strategy is None                     → "auto"   (chunk_size=800, chunk_overlap=400)
        │       request.chunking_strategy.type == "static"             → "static" (chunk_size/overlap từ request.static)
        │       request.chunking_strategy.type == "auto" (gửi tường minh) → rơi vào else → "fuse" (chunk_size=800, overlap=400)
        │           ⚠ "fuse" không nằm trong ("auto","static") nên Worker sẽ BỎ QUA bước embed/upsert —
        │             file vẫn được parse + chunk nhưng KHÔNG BAO GIỜ vào Qdrant, dù status vẫn báo "completed".
        │             Chỉ xảy ra khi client gửi tường minh {"type": "auto"} thay vì bỏ trống chunking_strategy.
        ├── process_vector_store_files.kiq(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap,
        │                                  request_id=request_id_ctx.get())   ← enqueue lên Redis stream broker (TaskIQ)
        └── return VectorStoreObject(status="in_progress")   ← trả response ngay, KHÔNG chờ ingest xong

        Note: chạy trên process taskiq_worker.py riêng, ngoài vòng đời request/response HTTP
        ▼
    process_vector_store_files(vectorstore_id, api_key, file_ids, chunking_strategy, chunk_size, chunk_overlap, request_id)
        │  request_id_ctx.set(request_id)   ← bind lại ContextVar trong process worker (xem mục Request correlation)
        │
        ├──▶ Bước 1: PostgresFileStore.check_existing_files(file_ids) → existing_file_ids
        │       existing_file_ids rỗng?  → usage_bytes giữ 0, bỏ qua Bước 2-3, nhảy thẳng Bước 4 (status=COMPLETED)
        │       ngược lại → get_total_bytes(file_ids) → usage_bytes ; get_metadata_for_files(existing_file_ids) → files_metadata
        │
        ├──▶ Bước 2: len(files_metadata) == 1 ?   ← chỉ hỗ trợ đúng MỘT file (TODO: multi-file processing)
        │       NO (0 hoặc >1)  → chunked_texts = []  (file dư/nhiều file bị bỏ qua âm thầm, không log cảnh báo)
        │       YES → load_and_chunk_file(minio_client, files_metadata[0], chunk_size)
        │               ParserFactory.get(ext)    .txt/.md → AsyncTextParser | .pdf → LlamaParseParser
        │                   không có parser cho ext?  → ValueError("Unsupported file format")
        │               MinioFileStore.download_file(bucket, path) → file_bytes (None → ValueError)
        │               parser.parse(file_bytes) → text
        │               ChonkieChunkingService(chunk_size).split_text(text) → chunked_texts
        │               lỗi ở bước nào? → _mark_failed(vectorstore_id, api_key, usage_bytes) [status=FAILED] → re-raise
        │
        ├──▶ Bước 3: chunking_strategy in ["auto","static"] AND chunked_texts không rỗng?
        │       NO  → bỏ qua embed/upsert hoàn toàn (xem ⚠ "fuse" ở trên)
        │       YES → embed_and_upload_chunks(qdrant_vector_store, chunked_texts, source_file_id=file_ids[0], vectorstore_id, api_key)
        │               chia batch theo EMBEDDING_UPLOAD_BATCH_SIZE (16)
        │               traced_span("/v1/vector_stores")   ← trace Langfuse bao toàn bộ ingest
        │               batch đầu tiên chạy MỘT MÌNH trước  ← tạo Qdrant collection trước, tránh race
        │                       get_dense_embedding(batch) → Document(page_content, metadata={"source": file_id})
        │                       → qdrant_vector_store.insert_documents(documents, embeddings, upload_batch_size)
        │               batch còn lại chạy song song (asyncio.gather), giới hạn bởi asyncio.Semaphore(EMBEDDING_BATCH_CONCURRENCY=4)
        │               lỗi ở bước nào? → _mark_failed(...) [status=FAILED] → re-raise
        │
        └──▶ Bước 4: PostgresVectorStore.update(status=UploadingStatus.COMPLETED, usage_bytes, last_active_at=now())

③ POST /v1/vector_stores/{id}/search   {query, max_num_results, filters, ranking_options}
        ▼
    VectorStoreService.search(vector_store_id, search_request, api_key)
        ├── validate_vector_store_prefix(id)    không có prefix "vs"?  → WrongPrefixVectorstoreException
        ├── PostgresVectorStore._check_vector_store_existence(id, api_key)   không tồn tại? → VectorStoreNotFoundException
        │
        │  qdrant_filter = None   ← TODO: search_request.filters KHÔNG được áp dụng (_normalize_qdrant_filter chưa implement)
        │  search_params dựng từ ranking_options nhưng KHÔNG được truyền vào retrieve() bên dưới  ← chưa có tác dụng
        │
        ├── traced_span("/v1/vector_stores/{id}/search")   Langfuse trace gốc (langfuse.trace.input={query, max_num_results})
        │       queries = [query] nếu là str, else query[:1]
        │           ⚠ nếu query là List[str], CHỈ phần tử đầu tiên được dùng — các query còn lại bị bỏ qua
        │
        │       traced_span("embedding")
        │               get_dense_embedding(queries) → queries_vectors
        │               embed_span: embedding.num_queries, embedding.model, embedding.dims
        │
        │       traced_span("retrieve")
        │               qdrant_service.client.collection_exists(vector_store_id)?
        │                   NO  → data=[]  (vd. đang ingest, hoặc ingest lỗi trước khi tạo collection — xem ⚠ "fuse" ở bước ②)
        │                   YES → qdrant_vector_store.retrieve(query_vectors=queries_vectors, query_filter=qdrant_filter, limit=max_num_results)
        │                         convert_query_response_to_search_results(retrieved_results)
        │                               payload["page_content"] → nối các dòng non-empty bằng " " (xoá whitespace/newline thừa)
        │                               payload["metadata"] → SearchResult.attributes
        │
        └── return VectorStoreSearchResponse(search_query, data, has_more = len(data) >= max_num_results)

        Lưu ý: `filters` và `ranking_options` được schema chấp nhận nhưng chưa áp dụng vào query Qdrant
               (xem [Design Decisions](DESIGN_DECISIONS_vi.md)).

Worker Shutdown  (TaskIQ WORKER_SHUTDOWN event, taskiq_worker.py)
        ├── postgres_service.close()          đóng asyncpg pool
        └── qdrant_service.client.close()     sync hoặc async, kiểm tra qua inspect.iscoroutinefunction
```