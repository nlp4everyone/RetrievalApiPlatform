# typing
from typing import Any, Dict, List, Literal, Optional
from pydantic import Field
# Other
from ..base.common import BaseModel, FilePurposes
from app.schemas.base.pagination import PaginatedResponse

class FileObject(BaseModel):
    """Represents a file in the system"""
    id: str = Field(description="The file identifier, which can be referenced in the API endpoints.")
    object: Literal["file"] = "file"
    bytes: int = Field(description="The size of the file, in bytes.")
    created_at: int = Field(description="The Unix timestamp (in seconds) for when the file was created.")
    filename: str = Field(description="The name of the file.")
    purpose: FilePurposes = Field(description="The intended purpose of the file. Supported values are assistants, assistants_output, batch, batch_output, fine-tune, fine-tune-results, vision, and user_data.")
    status: Optional[str] = Field(default=None,
                                  description="Deprecated. The current status of the file, which can be either uploaded, processed, or error.")
    status_details: Optional[Dict[str, Any]] = Field(default=None,
                                                     description="Deprecated. For details on why a fine-tuning training file failed validation, see the error field on fine_tuning.job.")
    expires_at: Optional[int] = Field(default=None,
                                      description="The Unix timestamp (in seconds) for when the file will expire.")

class FileListObject(PaginatedResponse[FileObject]):
    """Represents a paginated list of files"""
    object: Literal["list"] = "list"
    data: List[FileObject]
