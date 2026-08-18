from typing import Any, Optional
from datetime import datetime, timedelta
from fastapi import UploadFile
from app.schemas.file import FilePurposes, FileObject, FileListResponse, FileQueryRequest
from app.db.minio import MinioFileStore
from app.db.postgres import PostgresFileStore
from app.startup import get_io_executor, get_minio_service, get_postgres_pool
from app.core.config import UPLOADED_FILE_BUCKET
from app.utils.key_generator import generate_file_id
from app.utils.datetime_utils import convert_to_unix_timestamp
from app.exceptions import postgres_errors
from loggers import SystemLogger
import uuid

class FileService:
    """
    Service class for file management operations.
    
    This class provides static methods for file-related business logic, separating
    concerns from HTTP handling and database/storage implementations.
    """
    
    @staticmethod
    def _calculate_expiration(current_time: datetime,
                              expires_after_seconds: Optional[int]) -> tuple[Optional[int], Optional[datetime]]:
        """
        Calculate expiration timestamps for a file.
        
        Args:
            current_time: The current datetime as reference point
            expires_after_seconds: Number of seconds after which file should expire
            
        Returns:
            Tuple of (Unix timestamp, datetime object) for expiration, or (None, None)
            if no expiration is set
        """
        if not expires_after_seconds:
            return None, None
        expires_at_dt = current_time + timedelta(seconds=expires_after_seconds)
        expires_at_timestamp = convert_to_unix_timestamp(expires_at_dt)
        return expires_at_timestamp, expires_at_dt
    
    @staticmethod
    @postgres_errors("persisting file metadata")
    async def upload_file(file: UploadFile,
                          api_key: str,
                          purpose: FilePurposes,
                          expires_after_seconds: Optional[int] = None) -> FileObject:
        """
        Upload a file to MinIO storage and save metadata to PostgreSQL.
        
        This method handles the complete file upload workflow including validation,
        ID generation, storage upload, and metadata persistence.
        
        Args:
            file: The uploaded file object from FastAPI
            api_key: API key of the user uploading the file (used for path generation)
            purpose: Intended purpose of the file (e.g., 'assistants', 'fine-tune')
            expires_after_seconds: Optional seconds after which file should expire
            
        Returns:
            FileObject containing file metadata with Unix timestamps
            
        Raises:
            FileSizeLimitExceededException: If file exceeds MAX_FILE_SIZE
            Exception: If MinIO upload fails (propagated from storage layer)
        """
        # Get service dependencies
        minio_service = get_minio_service()
        postgres_pool = get_postgres_pool()
        
        # File size validation is now handled at router level via dependency
        file_size_bytes = file.size if file.size else 0
        
        # Generate unique identifiers and storage path
        unique_name = f"{uuid.uuid4().hex}_{file.filename}"
        object_path = f"{api_key}/uploads/{unique_name}"
        file_id = generate_file_id()
        
        # Calculate creation and expiration timestamps
        current_time = datetime.utcnow()
        created_at_timestamp = convert_to_unix_timestamp(current_time)
        expires_at_timestamp, expires_at_dt = FileService._calculate_expiration(
            current_time, expires_after_seconds
        )
        
        # Upload file to MinIO object storage
        try:
            result = await MinioFileStore.upload_file(
                minio_client=minio_service.client,
                file_buffer=file.file,
                file_size=file_size_bytes,
                file_name=object_path,
                bucket_name=UPLOADED_FILE_BUCKET,
                content_type=file.content_type,
                executor=get_io_executor()
            )
            SystemLogger.info(f"File uploaded successfully to MinIO: {object_path}")
        except Exception as e:
            SystemLogger.error(f"Failed to upload file to MinIO: {e}", exc_info=True)
            raise

        # Persist file metadata to PostgreSQL
        await PostgresFileStore.insert_file(
            pool=postgres_pool,
            id=file_id,
            api_key=api_key,
            bytes=file_size_bytes,
            purpose=purpose,
            created_at=current_time,
            expires_at=expires_at_dt,
            content_type=file.content_type,
            metadata={
                "filename": file.filename,
                "minio_bucket": result.get("bucket"),
                "minio_path": result.get("object"),
                "etag": result.get("etag")
            }
        )

        SystemLogger.info(f"File metadata persisted: {file_id}")

        # Return file object with Unix timestamps for API compatibility
        return FileObject(
            id=file_id,
            bytes=file_size_bytes,
            created_at=created_at_timestamp,
            expires_at=expires_at_timestamp,
            filename=file.filename,
            purpose=purpose
        )
    
    @staticmethod
    @postgres_errors("listing files")
    async def list_files(api_key: str,
                         query: FileQueryRequest) -> FileListResponse:
        """
        List files for an API key with optional filtering and pagination.
        
        Args:
            api_key: API key to filter files by ownership
            query: Query parameters including purpose filter, pagination cursor,
                   limit, and sort order
                   
        Returns:
            FileListResponse containing paginated list of file objects with
            pagination metadata (first_id, last_id, has_more)
        """
        postgres_pool = get_postgres_pool()

        # Query files from database with filters and pagination
        results = await PostgresFileStore.list_files(
            pool=postgres_pool,
            api_key=api_key,
            purpose=query.purpose,
            after=query.after,
            limit=query.limit,
            order=query.order
        )

        # Convert database results to FileObject domain models
        file_objects = [
            FileObject(
                id=result.get("id"),
                bytes=result.get("bytes"),
                created_at=convert_to_unix_timestamp(result.get("created_at")),
                expires_at=convert_to_unix_timestamp(result.get("expires_at")),
                filename=result.get("metadata", {}).get("filename"),
                purpose=result.get("purpose")
            )
            for result in results
        ]

        SystemLogger.debug(f"Listed {len(file_objects)} file(s)")

        # Build pagination metadata
        return FileListResponse(
            data=file_objects,
            first_id=file_objects[0].id if file_objects else None,
            last_id=file_objects[-1].id if file_objects else None,
            has_more=len(file_objects) > 0 and len(file_objects) == query.limit
        )

    @staticmethod
    @postgres_errors("getting file {file_id}")
    async def get_file_by_id(file_id: str,
                             api_key: str) -> FileObject:
        """
        Retrieve a single file by its ID.

        Args:
            file_id: Unique file identifier to retrieve
            api_key: API key that owns the file (for security)

        Returns:
            FileObject containing file metadata with Unix timestamps

        Raises:
            FileNotFoundException: If file does not exist or doesn't belong to the API key (propagated from DB layer)
        """
        postgres_pool = get_postgres_pool()

        # Fetch file metadata from database
        result = await PostgresFileStore.get_file_by_id(pool=postgres_pool,
                                                        file_id=file_id,
                                                        api_key=api_key)

        SystemLogger.debug(f"File retrieved: {file_id}")

        # Convert to domain model with timestamp conversion
        return FileObject(
            id=result.get("id"),
            bytes=result.get("bytes"),
            created_at=convert_to_unix_timestamp(result.get("created_at")),
            expires_at=convert_to_unix_timestamp(result.get("expires_at")),
            filename=result.get("metadata", {}).get("filename"),
            purpose=result.get("purpose")
        )

    @staticmethod
    @postgres_errors("deleting file {file_id}")
    async def delete_file(file_id: str,
                          api_key: str) -> dict[str, Any]:
        """
        Delete a file from both PostgreSQL metadata and MinIO storage.
        
        This method performs a cascading deletion: first removes the database record
        (which includes ownership validation via api_key), then attempts to delete the
        actual file from MinIO storage. Storage deletion failures are logged but don't
        prevent the database deletion from succeeding.
        
        Args:
            file_id: Unique file identifier to delete
            api_key: API key for ownership validation
            
        Returns:
            Dictionary containing file_id and deletion status
            
        Raises:
            FileNotFoundException: If file does not exist or doesn't belong to api_key
        """
        postgres_pool = get_postgres_pool()
        minio_service = get_minio_service()

        # Delete from database (includes ownership validation)
        result = await PostgresFileStore.delete_file_by_id(
            pool=postgres_pool,
            file_id=file_id,
            api_key=api_key
        )

        SystemLogger.info(f"File deleted: {file_id}")

        # Extract storage location from metadata
        metadata = result.get("metadata", {})
        deleted_path = metadata.get("minio_path")
        file_bucket = metadata.get("minio_bucket")

        # Attempt to delete from MinIO storage (best-effort)
        if deleted_path and file_bucket:
            try:
                await MinioFileStore.delete_file(
                    minio_client=minio_service.client,
                    file_path=deleted_path,
                    bucket_name=file_bucket,
                    executor=get_io_executor()
                )
                SystemLogger.info(f"File deleted successfully from MinIO: {deleted_path}")
            except Exception as e:
                # Log but don't fail - database deletion already succeeded
                SystemLogger.error(f"Failed to delete file from MinIO: {e}", exc_info=True)

        return {"id": file_id, "deleted": True}
