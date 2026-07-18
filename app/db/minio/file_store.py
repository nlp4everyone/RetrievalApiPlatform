from typing import Optional, Dict, Any, Union, BinaryIO
# Minio component
from minio import Minio
from minio.error import S3Error
import asyncio, io
# Logger
from loggers import SystemLogger

class MinioFileStore:
    @staticmethod
    async def upload_file(minio_client: Minio,
                          file_buffer: Union[bytes, bytearray, BinaryIO],
                          file_name: str,
                          bucket_name: str,
                          content_type: str,
                          file_size: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Upload a file to a Minio bucket with streaming support.
        
        Args:
            minio_client: Configured Minio client instance
            file_buffer: File data as bytes, bytearray, or file-like object (for streaming)
            file_name: Name to assign to the file in the bucket
            bucket_name: Target bucket name
            content_type: MIME type of the file (e.g., 'application/pdf')
            file_size: File size in bytes (required for file-like objects)
            
        Returns:
            dict: Upload result containing bucket, object name, and etag on success
            None: On upload failure
            
        Note:
            - For bytes/bytearray: uploads in single operation
            - For file-like objects: MinIO handles streaming automatically
        """
        try:
            # Handle different input types
            if isinstance(file_buffer, (bytes, bytearray)):
                # Legacy support for bytes input
                file_size_bytes = len(file_buffer)
                result = await asyncio.to_thread(minio_client.put_object,
                                                 bucket_name,
                                                 file_name,
                                                 data=io.BytesIO(file_buffer),
                                                 length=file_size_bytes,
                                                 content_type=content_type)
            else:
                # Streaming upload with file object
                if file_size is None:
                    raise ValueError("file_size is required when using file-like objects")
                
                result = await asyncio.to_thread(minio_client.put_object,
                                                 bucket_name,
                                                 file_name,
                                                 data=file_buffer,
                                                 length=file_size,
                                                 content_type=content_type)

            # Return upload success information
            return {
                "message": "Upload successful",
                "bucket": result.bucket_name,
                "object": result.object_name,
                "etag": result.etag,
            }

        except S3Error as e:
            # Log upload failure with specific error details
            SystemLogger.error(f"Failed when trying to upload file: {file_name}")

    @staticmethod
    async def delete_file(minio_client: Minio,
                          bucket_name: str,
                          file_path: str) -> Optional[Dict[str, Any]]:
        """
        Delete a file from a Minio bucket.
        
        Args:
            minio_client: Configured Minio client instance
            bucket_name: Bucket containing the file
            file_path: Path to the file within the bucket
            
        Returns:
            dict: Deletion confirmation with bucket and path on success
            None: On deletion failure
        """
        try:
            # Execute the blocking delete operation in a thread pool to avoid blocking
            await asyncio.to_thread(minio_client.remove_object, bucket_name, file_path)
            return {"deleted": True, "bucket": bucket_name, "path": file_path}
        except S3Error as e:
            # Log deletion failure with specific error details
            SystemLogger.error(f"Failed when trying to delete file: {file_path} in bucket {bucket_name}")

    @staticmethod
    async def download_file(minio_client: Minio,
                           bucket_name: str,
                           file_path: str) -> Optional[bytes]:
        """
        Download a file from a Minio bucket.
        
        Args:
            minio_client: Configured Minio client instance
            bucket_name: Bucket containing the file
            file_path: Path to the file within the bucket
            
        Returns:
            bytes: File content on success
            None: On download failure
            
        Note:
            Properly cleans up network resources in finally block
        """
        # Get object from Minio in a thread pool to avoid blocking the event loop
        response = await asyncio.to_thread(minio_client.get_object, bucket_name, file_path)
        try:
            # Read the file content from the response stream
            return response.read()
        except S3Error as e:
            # Log download failure with specific error details
            SystemLogger.error(f"Failed when trying to download file: {file_path} in bucket {bucket_name}")
            return None
        finally:
            # Always clean up network connections and resources to prevent leaks
            response.close()
            response.release_conn()
