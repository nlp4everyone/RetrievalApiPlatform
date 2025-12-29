from pydantic import BaseModel as PydanticBaseModel
from typing import Optional, TypeVar
from enum import Enum
from typing import Literal

T = TypeVar('T')

class BaseModel(PydanticBaseModel):
    """Base model with common configuration for all schemas"""
    class Config:
        extra = 'forbid'
        use_enum_values = True
        json_encoders = {
            # Add custom JSON encoders here
        }

class BaseResponse(BaseModel):
    """Base response model with common fields"""
    id: str
    object: str
    created_at: Optional[int] = None

class DeletedResponse(BaseResponse):
    """Standard response for deleted objects"""
    deleted: bool = True

FilePurposes = Literal["assistants","batch","fine-tune","vision","user_data","evals"]

class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"
