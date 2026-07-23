"""Download, parse, and chunk a single stored file into text chunks."""
from pathlib import Path
from typing import Optional

from app.schemas.file.types import FileFormat
from app.db.minio import MinioFileStore
from app.services.parsers import ParserFactory
from app.services.chunking.chonkie_chunker import ChonkieChunkingService, ChonkieChunkingConfig
from app.utils.io import async_temp_file


async def load_and_chunk_file(minio_client, file_metadata: dict, chunk_size: Optional[int]) -> list[str]:
    """Download the file from MinIO, parse its content, and split it into chunks."""
    # Step 1: Pick the parser for this file's extension
    file_ext = Path(file_metadata.get("filename")).suffix
    current_file_format, parser = ParserFactory.get(file_type=file_ext)
    if current_file_format is None or parser is None:
        raise ValueError(f"Unsupported file format: {file_ext}")

    # Step 2: Download raw bytes from MinIO
    file_bytes = await MinioFileStore.download_file(minio_client=minio_client,
                                                     bucket_name=file_metadata.get("minio_bucket"),
                                                     file_path=file_metadata.get("minio_path"))

    # Step 3: Parse content according to its format
    if current_file_format == FileFormat.TEXT:
        file_content = await parser.parse(file_input=file_bytes)
    elif current_file_format == FileFormat.PDF:
        async with async_temp_file(file_bytes, suffix=".pdf") as path:
            file_content = await parser.parse(file_input=path)

    # Step 4: Split parsed content into chunks
    chunking_service = ChonkieChunkingService(config=ChonkieChunkingConfig(chunk_size=chunk_size))
    return chunking_service.split_text(text=file_content)