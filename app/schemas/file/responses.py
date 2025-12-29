# Typing
from typing import List, Literal, Optional
from pydantic import Field, HttpUrl
# Other
from ..base.common import DeletedResponse
from app.schemas.base.pagination import PaginatedResponse
from .models import FileObject

class FileDeletedResponse(DeletedResponse):
    """Response model for deleted files"""
    object: Literal["file"] = "file"

class FileUploadResponse(FileObject):
    """Response model for file uploads"""
    url: Optional[HttpUrl] = Field(default=None,
                                   description="URL to access the uploaded file")

class FileListResponse(PaginatedResponse[FileObject]):
    """Response model for listing files with pagination"""
    object: Literal["list"] = "list"
    data: List[FileObject]
