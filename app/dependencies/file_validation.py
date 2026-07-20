from fastapi import UploadFile, HTTPException, Depends
from app.core.config import MAX_FILE_SIZE
from app.exceptions.file import FileSizeLimitExceededException


async def validate_file_size(file: UploadFile) -> UploadFile:
    """
    Validate file size before processing.
    
    This dependency function checks if the uploaded file exceeds the maximum
    allowed size and raises an appropriate exception if it does.
    
    Args:
        file: The uploaded file object from FastAPI
        
    Returns:
        The validated file object
        
    Raises:
        FileSizeLimitExceededException: If file size exceeds MAX_FILE_SIZE
    """
    file_size_bytes = file.size if file.size else 0
    file_size_mb = file_size_bytes / (1024 * 1024)
    
    if file_size_mb > MAX_FILE_SIZE:
        raise FileSizeLimitExceededException(max_size=MAX_FILE_SIZE, current_size=file_size_mb)
    
    return file
