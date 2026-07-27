import os
from fastapi import UploadFile, HTTPException, status
from app.core.config import MAX_FILE_SIZE, ALLOWED_MIME_TYPES, ALLOWED_EXTENSIONS, MIME_TYPE_MAPPING
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


async def validate_file_type(file: UploadFile) -> UploadFile:
    """
    Validate file type based on MIME type and extension.
    
    Args:
        file: The uploaded file object from FastAPI
        
    Returns:
        The validated file object
        
    Raises:
        HTTPException: If file type is not allowed
    """
    # Validate MIME type
    if file.content_type and file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Content type '{file.content_type}' is not allowed. Allowed types: {ALLOWED_MIME_TYPES}"
        )
    
    # Validate file extension
    if file.filename:
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"File extension '{file_ext}' is not allowed. Allowed extensions: {ALLOWED_EXTENSIONS}"
            )
        
        # Validate MIME type matches extension
        if file.content_type:
            expected_ext = MIME_TYPE_MAPPING.get(file.content_type)
            if expected_ext and file_ext != expected_ext:
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail=f"MIME type '{file.content_type}' does not match file extension '{file_ext}'"
                )
    
    return file


async def validate_file(file: UploadFile) -> UploadFile:
    """
    Combined validation for file size and type.
    
    This dependency function performs both file size and file type validation
    in a single call for convenience.
    
    Args:
        file: The uploaded file object from FastAPI
        
    Returns:
        The validated file object
        
    Raises:
        FileSizeLimitExceededException: If file size exceeds MAX_FILE_SIZE
        HTTPException: If file type is not allowed
    """
    file = await validate_file_type(file)
    file = await validate_file_size(file)
    return file
