# Minio component
from minio import Minio
from minio.error import S3Error
import asyncio, io
# Logger
from loggers import SystemLogger

class MinioFileStore:
    @staticmethod
    async def upload_file(minio_client :Minio,
                          file_buffer,
                          file_name :str,
                          bucket_name :str,
                          content_type:str):
        try:
            # Get file size
            file_size_bytes = len(file_buffer)
            # Convert to mb
            file_size_mb = file_size_bytes / (1024 * 1024)

            if file_size_mb < 50:
                # File size small (Less than 50MB)
                result = await asyncio.to_thread(minio_client.put_object,
                                                 bucket_name,
                                                 file_name,
                                                 data = io.BytesIO(file_buffer),
                                                 length = file_size_bytes,
                                                 content_type = content_type)
            else:
                # Large file size
                result = await asyncio.to_thread(minio_client.put_object,
                                                 bucket_name,
                                                 file_name,
                                                 file_buffer,
                                                 length = -1,
                                                 part_size = 10 * 1024 * 1024,
                                                 content_type = content_type)

                # ✅ Upload succeeded if no exception and we got result info
            return {
                "message": "Upload successful",
                "bucket": result.bucket_name,
                "object": result.object_name,
                "etag": result.etag,
            }

        except S3Error as e:
            SystemLogger.error(f"Failed when trying to upload file: {file_name}")

    @staticmethod
    async def delete_file(minio_client :Minio,
                          bucket_name: str,
                          file_path: str):
        try:
            # Run the blocking I/O call in a thread
            await asyncio.to_thread(minio_client.remove_object, bucket_name, file_path)
            return {"deleted": True, "bucket": bucket_name, "path": file_path}
        except S3Error as e:
            SystemLogger.error(f"Failed when trying to delete file: {file_path} in bucket {bucket_name}")

    @staticmethod
    async def _load_file(minio_client: Minio,
                         bucket_name: str,
                         file_path: str):
        response = await asyncio.to_thread(minio_client.get_object, bucket_name, file_path)
        try:
            # Run the blocking I/O call in a thread
            return response.read()
        except S3Error as e:
            SystemLogger.error(f"Failed when trying to delete file: {file_path} in bucket {bucket_name}")
        finally:
            response.close()
            response.release_conn()
