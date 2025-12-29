# Typing
from typing import Dict, Literal, Optional, List
from pydantic import Field, BaseModel, constr
# Dependencies
from ..types import VectorStoreType
from .base import VectorStoreFileCounts, VectorStoreExpiresAfter


class VectorStoreObject(BaseModel):
    """Represents a vector store as provided by the API."""
    id: str = Field(...,
                    description="The identifier, which can be referenced in API endpoints.")
    object: Literal["vector_store"] = Field(default="vector_store",
                                            description="The object type, which is always 'vector_store'.")
    created_at: int = Field(...,
                            description="The Unix timestamp (in seconds) for when the vector store was created.")
    expires_after: Optional[VectorStoreExpiresAfter] = Field(None,
                                                             description="The expiration policy for a vector store.")
    expires_at: Optional[int] = Field(None,
                                      description="The Unix timestamp (in seconds) for when the vector store will expire.")
    file_counts: VectorStoreFileCounts = Field(...,
                                               description="The file counts for the vector store.")
    last_active_at: int = Field(...,
                                description="The Unix timestamp (in seconds) for when the vector store was last active.")
    metadata: Optional[Dict[
        constr(max_length=64),  # Keys: max 64 chars
        constr(max_length=512)  # Values: max 512 chars
    ]] = Field(
        None,
        max_items=16,
        description=("Set of 16 key-value pairs that can be attached to an object. "
                   "This can be useful for storing additional information about the object "
                   "in a structured format, and querying for objects via API or the dashboard. "
                   "Keys are strings with a maximum length of 64 characters. "
                   "Values are strings with a maximum length of 512 characters.")
    )
    name: Optional[str] = Field(None,
                                description="The name of the vector store.")
    status: Literal["expired", "in_progress", "completed"] = Field(...,
                                                                   description=("The status of the vector store, which can be either 'expired', 'in_progress', "
                                                                                "or 'completed'. A status of 'completed' indicates that the vector store is ready for use."))
    usage_bytes: int = Field(...,
                             description="The total number of bytes used by the files in the vector store.",
                             ge=0)

class ListVectorStoreObject(BaseModel):
    """Represents a list of vector stores with pagination support."""
    object: Literal["list"] = Field("list",
                                    description="The object type, which is always 'list'.")
    data: List[VectorStoreObject] = Field(...,
                                          description="The list of vector stores.")
    first_id: Optional[str] = Field(default=None,
                                    description="The ID of the first vector store in the list.")
    last_id: Optional[str] = Field(default=None,
                                   description="The ID of the last vector store in the list.")
    has_more: bool = Field(...,
                           description="Whether there are more vector stores available.")
