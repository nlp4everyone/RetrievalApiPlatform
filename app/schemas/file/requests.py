# Typing
from typing import Optional, Union
from pydantic import Field, FilePath
# Other
from ..base.pagination import PaginationParams
from ..base.common import FilePurposes

class FileUploadRequest(PaginationParams):
    """Request model for file uploads"""
    file: Union[bytes, FilePath] = Field(...,
                                         description="The file content or path to upload")
    purpose: FilePurposes = Field(...,
                                  description="The intended purpose of the uploaded file")
    metadata: Optional[dict] = Field(default=None,
                                     description="Optional metadata to attach to the file")

class FileQueryRequest(PaginationParams):
    """Request model for querying files with pagination support"""
    purpose: Optional[str] = Field(default=None,
                                   description="Only return files with the given purpose")
