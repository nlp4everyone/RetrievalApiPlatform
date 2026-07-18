# FastAPI dependencies
from fastapi import UploadFile, Form, APIRouter, Depends, File
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
from app.core.config import *
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
                      file: UploadFile = File(..., description="The file to upload"),
                      expires_after_anchor: Optional[str] = Form(None, alias="expires_after[anchor]", description="Anchor point for expiration time calculation"),
                      expires_after_seconds: Optional[int] = Form(None, alias="expires_after[seconds]", description="Number of seconds after which the file will expire"),
                      api_key: str = Depends(verify_api_key)):
    """
    ## Upload a file that can be used across various endpoints.

    ### Args:
    - `purpose`: The intended use case for the uploaded file.
    - `file`: The file to be uploaded.
    - `expires_after_anchor`: Optional anchor point for expiration time calculation.
    - `expires_after_seconds`: Optional duration in seconds after which the file will expire.

    Reference: [OpenAI Create File API](https://developers.openai.com/api/reference/resources/files/methods/create)

    """
    # Get MinIO service
    minio_service = get_minio_service()
    # Get PostgreSQL connection pool
    postgres_pool = get_postgres_pool()
    # Get file size without loading content into memory
    file_size_bytes = file.size if file.size else 0
    file_size_mb = file_size_bytes / (1024 * 1024)

    # Check if file size exceeds limit
    if file_size_mb > MAX_FILE_SIZE: raise FileSizeLimitExceededException(max_size = MAX_FILE_SIZE, current_size = file_size_mb)

    # Generate unique file path
    unique_name = f"{uuid.uuid4().hex}_{file.filename}"
    object_path = f"{api_key}/uploads/{unique_name}"

    # Get current time and calculate expiration
    current_time = datetime.datetime.utcnow()
    created_at_timestamp = int(current_time.timestamp())
    expires_at_timestamp = created_at_timestamp + expires_after_seconds if expires_after_seconds else None
    expires_at_dt = current_time + datetime.timedelta(seconds=expires_after_seconds) if expires_after_seconds else None

    # Generate unique file ID
    file_id = generate_file_id()
    # Upload file to MinIO storage
    try:
        result = await MinioFileStore.upload_file(minio_client = minio_service.client,
                                                  file_buffer = file.file,
                                                  file_size = file_size_bytes,
                                                  file_name = object_path,
                                                  bucket_name = UPLOADED_FILE_BUCKET,
                                                  content_type = file.content_type)
        SystemLogger.info(f"File uploaded successfully to MinIO: {object_path}")
    except Exception as e:
        SystemLogger.error(f"Failed to upload file to MinIO: {str(e)}")
        raise

    # Save file metadata to PostgreSQL
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

    # Return file object with Unix timestamps
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
    ## Returns a list of files.

    ### Args:
    - `purpose`: Only return files with the given purpose.
    - `after`: A cursor for use in pagination. after is an object ID that defines your place in the list
    - `limit`: A limit on the number of objects to be returned. Limit can range between 1 and 10,000, and the default is 10,000.
    - `order`: Sort order by the created_at timestamp of the objects. asc for ascending order and desc for descending order.

    Reference: [OpenAI List Files API](https://developers.openai.com/api/reference/resources/files/methods/list)

    """
    # Get PostgreSQL connection pool
    postgres_pool = get_postgres_pool()

    # Query files from database
    results = await PostgresFileStore.list_files(pool = postgres_pool,
                                                 api_key = api_key,
                                                 purpose = query.purpose,
                                                 after = query.after,
                                                 limit = query.limit,
                                                 order = query.order)

    # Construct file objects from database results
    file_objects = []
    for result in results:
        # Convert datetime to Unix timestamp
        created_at = int(result.get("created_at").timestamp())
        expires_at = int(result.get("expires_at").timestamp()) if result.get("expires_at") else None

        # Add file object to list
        file_objects.append(FileObject(id=result.get("id"),
                                       bytes=result.get("bytes"),
                                       created_at=created_at,
                                       expires_at=expires_at,
                                       filename=result.get("metadata", {}).get("filename"),
                                       purpose=result.get("purpose")))

    # Return paginated file list
    return FileListResponse(data=file_objects,
                            first_id=file_objects[0].id if file_objects else None,
                            last_id=file_objects[-1].id if file_objects else None,
                            has_more=len(file_objects) > 0 and len(file_objects) == query.limit)

@file_router.get("/files/{file_id}", response_model = FileObject)
async def get_file_by_id(file_id: str,
                         api_key: str = Depends(verify_api_key)) -> FileObject:
    """
    ## Returns information about a specific file.

    ### Args:
    - `file_id`: The unique identifier of the file to retrieve.

    Reference: [OpenAI Retrieve File API](https://developers.openai.com/api/reference/resources/files/methods/retrieve)

    """
    # Get PostgreSQL connection pool
    postgres_pool = get_postgres_pool()
    
    # Fetch file metadata from database
    result = await PostgresFileStore.get_file_by_id(pool=postgres_pool,
                                                    file_id=file_id)
    
    # Convert timestamps to Unix format
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
    ## Delete a file and remove it from all vector stores.

    ### Args:
    - `file_id`: The unique identifier of the file to delete.

    Reference: [OpenAI Delete File API](https://developers.openai.com/api/reference/resources/files/methods/delete)

    """
    # Get PostgreSQL connection pool
    postgres_pool = get_postgres_pool()
    
    # Get MinIO service for storage operations
    minio_service = get_minio_service()

    # Delete file metadata from database
    result = await PostgresFileStore.delete_file_by_id(pool=postgres_pool,
                                                       file_id=file_id,
                                                       api_key=api_key)

    # Extract file location from metadata
    metadata = result.get("metadata", {})
    deleted_path = metadata.get("minio_path")
    file_bucket = metadata.get("minio_bucket")

    # Delete file from MinIO storage
    if deleted_path and file_bucket:
        try:
            await MinioFileStore.delete_file(minio_client=minio_service.client,
                                             file_path=deleted_path,
                                             bucket_name=file_bucket)
            SystemLogger.info(f"File deleted successfully from MinIO: {deleted_path}")
        except Exception as e:
            SystemLogger.error(f"Failed to delete file from MinIO: {str(e)}")
            # Continue with database deletion even if MinIO deletion fails
    # Return deletion response
    return FileDeletedResponse(id=file_id)
