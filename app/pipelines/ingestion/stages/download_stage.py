"""Stage 1 - fetch the raw file bytes from object storage."""
from typing import Any, ClassVar

from minio import Minio

from app.db.minio import MinioFileStore
from app.pipelines.ingestion.base import BaseIngestionStage
from app.pipelines.ingestion.context import IngestionContext
from app.startup import get_io_executor, get_storage_semaphore


class DownloadStage(BaseIngestionStage):
    """Download the uploaded file from MinIO into memory."""

    name: ClassVar[str] = "download"

    def __init__(self, minio_client: Minio) -> None:
        """
        Args:
            minio_client: Connected MinIO client
        """
        self._minio_client = minio_client

    async def run(self, context: IngestionContext) -> None:
        """
        Populate context.raw_bytes from the file's recorded bucket and path.

        Raises:
            ValueError: If the object could not be read
        """
        # Bounded so a burst of concurrent ingestion jobs in this worker
        # process can't alone exhaust the I/O thread pool
        async with get_storage_semaphore():
            file_bytes = await MinioFileStore.download_file(minio_client = self._minio_client,
                                                            bucket_name = context.storage_bucket,
                                                            file_path = context.storage_path,
                                                            executor = get_io_executor())
        if file_bytes is None:
            raise ValueError(f"Failed to download file: {context.storage_path}")

        context.raw_bytes = file_bytes

    def span_attributes(self, context: IngestionContext) -> dict[str, Any]:
        return {
            "storage.type": "minio",
            "storage.bucket": context.storage_bucket,
            "storage.path": context.storage_path,
            "file.name": context.filename,
            "file.size_bytes": len(context.raw_bytes or b""),
        }