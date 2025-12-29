# Fast API dependencies
from fastapi import UploadFile, Form, APIRouter, Depends
# Typing
from typing import Optional
# Schema
from app.schemas.file import (FilePurposes,
                              FileObject,
                              FileListResponse,
                              FileQueryRequest,
                              FileDeletedResponse)
# Exception
from app.exceptions.file import FileSizeLimitExceededException
# Minio
from app.db.minio import MinioFileStore
from app.startup import get_minio_service, get_postgres_pool
# Postgres
from app.db.postgres import PostgresFileStore
# Config
from app.core.config.constants import MAX_FILE_SIZE, UPLOADED_FILE_BUCKET
# Security
from app.security.auth import verify_api_key
from app.utils.key_generator import generate_file_id
# Other components
import uuid, datetime
# Logger
from loggers import SystemLogger

# Router
file_router = APIRouter()

@file_router.post("/files", response_model = FileObject)
async def upload_file(purpose: FilePurposes = Form(..., description="The intended purpose of the uploaded file. Must be one of: assistants, batch, fine-tune, vision, user_data, evals"),
                      file: UploadFile = Form(..., description="The file to upload"),
                      expires_after_anchor: Optional[str] = Form(None, alias="expires_after[anchor]", description="Anchor point for expiration time calculation"),
                      expires_after_seconds: Optional[int] = Form(None, alias="expires_after[seconds]", description="Number of seconds after which the file will expire"),
                      api_key: str = Depends(verify_api_key)):
    """
    ## Upload a file to the file storage system.

    This endpoint allows users to upload files that can be used for various purposes like
    fine-tuning, vision tasks, or as user data. The file is stored in both MinIO object storage
    and its metadata is recorded in PostgreSQL.

    ### Args:
    - `purpose`: The intended use case for the uploaded file.
    - `file`: The file to be uploaded.
    - `expires_after_anchor`: Optional anchor point for expiration time calculation.
    - `expires_after_seconds`: Optional duration in seconds after which the file will expire.
    """
    # Get Minio service
    minio_service = get_minio_service()
    # Pool
    postgres_pool = get_postgres_pool()
    # Read file content
    file_bytes = await file.read()
    # Get file size
    file_size_bytes = len(file_bytes)
    # Convert to mb
    file_size_mb = file_size_bytes / (1024 * 1024)

    # When file size too large
    if file_size_mb > MAX_FILE_SIZE: raise FileSizeLimitExceededException(max_size = MAX_FILE_SIZE, current_size = file_size_mb)

    # Generate folder-like path
    unique_name = f"{uuid.uuid4().hex}_{file.filename}"
    object_path = f"{api_key}/uploads/{unique_name}"

    # Get current time
    current_time = datetime.datetime.utcnow()
    # Convert to Unix timestamp for response
    created_at_timestamp = int(current_time.timestamp())
    # Calculate expires_at if expires_after_seconds is provided
    expires_at_timestamp = created_at_timestamp + expires_after_seconds if expires_after_seconds else None
    # Convert to datetime for Postgres
    expires_at_dt = current_time + datetime.timedelta(seconds=expires_after_seconds) if expires_after_seconds else None

    # Define file id
    file_id = generate_file_id()
    # Upload file
    result = await MinioFileStore.upload_file(minio_client = minio_service.client,
                                              file_buffer = file_bytes,
                                              file_name = object_path,
                                              bucket_name = UPLOADED_FILE_BUCKET,
                                              content_type = file.content_type)

    # Upload to Postgres with datetime objects
    await PostgresFileStore.insert_file(pool=postgres_pool,
                                        id=file_id,
                                        api_key=api_key,
                                        bytes=file_size_bytes,
                                        purpose=purpose,
                                        created_at=current_time,
                                        expires_at=expires_at_dt,
                                        content_type=file.content_type,
                                        metadata={"filename": file.filename,
                                                  "minio_bucket": result.get("bucket"),
                                                  "minio_path": result.get("object"),
                                                  "etag": result.get("etag")})

    # Return response with Unix timestamps
    return FileObject(id=file_id,
                      bytes=file_size_bytes,
                      created_at=created_at_timestamp,
                      expires_at=expires_at_timestamp,
                      filename=file.filename,
                      purpose=purpose)

@file_router.get("/files", response_model = FileListResponse)
async def list_files(query: FileQueryRequest = Depends(),
                     api_key: str = Depends(verify_api_key)):
    """
    ## List files with optional filtering and pagination.
    Retrieves a paginated list of files that the authenticated user has uploaded.
    Results can be filtered by purpose and are sorted by creation date in descending order by default.

    ### Args:
    - `purpose`: Optional filter by file purpose
    - `after`: Cursor for pagination (file ID)
    - `limit`: Maximum number of files to return (default: 20, max: 100)
    - `order`: Sort order ('asc' or 'desc')
    """
    # Pool
    postgres_pool = get_postgres_pool()

    # Upload to Postgres
    results = await PostgresFileStore.list_files(pool = postgres_pool,
                                                 api_key = api_key,
                                                 purpose = query.purpose,
                                                 after = query.after,
                                                 limit = query.limit,
                                                 order = query.order)

    # Construct file objects with Unix timestamps
    file_objects = []
    for result in results:
        # Convert datetime objects to Unix timestamps (seconds since epoch)
        created_at = int(result.get("created_at").timestamp())
        expires_at = int(result.get("expires_at").timestamp()) if result.get("expires_at") else None

        # Append
        file_objects.append(FileObject(id=result.get("id"),
                                       bytes=result.get("bytes"),
                                       created_at=created_at,
                                       expires_at=expires_at,
                                       filename=result.get("metadata", {}).get("filename"),
                                       purpose=result.get("purpose")))

    # Return paginated response
    return FileListResponse(data=file_objects,
                            first_id=file_objects[0].id if file_objects else None,
                            last_id=file_objects[-1].id if file_objects else None,
                            has_more=len(file_objects) > 0 and len(file_objects) == query.limit)

@file_router.get("/files/{file_id}", response_model = FileObject)
async def get_file_by_id(file_id: str,
                         api_key: str = Depends(verify_api_key)) -> FileObject:
    """
    ## Retrieve a single file by its ID.
    Fetches the file metadata from the database and returns it in a structured format.

    ### Args:
    - `file_id`: The unique identifier of the file to retrieve.
    """
    # Get database connection pool
    postgres_pool = get_postgres_pool()
    
    # Fetch file metadata from database
    result = await PostgresFileStore.get_file_by_id(pool=postgres_pool,
                                                    file_id=file_id)
    
    # Convert timestamps to Unix timestamps (seconds since epoch)
    created_at = int(result.get("created_at").timestamp())
    expires_at = int(result.get("expires_at").timestamp()) if result.get("expires_at") else None
    
    return FileObject(id=result.get("id"),
                      bytes=result.get("bytes"),
                      created_at=created_at,
                      expires_at=expires_at,
                      filename=result.get("metadata", {}).get("filename"),
                      purpose=result.get("purpose"))

@file_router.delete("/files/{file_id}", response_model = FileDeletedResponse)
async def delete_file(file_id: str,
                      api_key: str = Depends(verify_api_key)) -> FileDeletedResponse:
    """
    ## Delete a file by its ID.
    Removes the file's metadata from the database and the actual file from object storage.
    The operation is atomic - if either the database or storage operation fails,
    the entire operation is rolled back.

    ### Args:
    - `file_id`: The unique identifier of the file to delete.
    """
    # Get database connection pool
    postgres_pool = get_postgres_pool()
    
    # Get MinIO service for file storage operations
    minio_service = get_minio_service()

    # Delete file metadata from database (this verifies ownership via api_key)
    result = await PostgresFileStore.delete_file_by_id(pool=postgres_pool,
                                                       file_id=file_id,
                                                       api_key=api_key)

    # Extract storage location from metadata
    metadata = result.get("metadata", {})
    deleted_path = metadata.get("minio_path")
    file_bucket = metadata.get("minio_bucket")

    # Delete the actual file from MinIO storage
    if deleted_path and file_bucket:
        await MinioFileStore.delete_file(minio_client=minio_service.client,
                                         file_path=deleted_path,
                                         bucket_name=file_bucket)
    # Return
    return FileDeletedResponse(id=file_id)
