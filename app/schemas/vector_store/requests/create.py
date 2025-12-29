# Typing
from typing import Dict, List, Optional
from pydantic import Field, BaseModel
# Dependencies
from .base import ChunkingStrategy, ExpiresAfter

class VectorStoreCreateRequest(BaseModel):
    """Request model for creating a new vector store."""
    name: Optional[str] = Field(default=None,
                                description="The name of the vector store.")
    description: Optional[str] = Field(default=None,
                                       description="A description for the vector store. "
                                                   "Can be used to describe the vector store's purpose.")
    chunking_strategy: Optional[ChunkingStrategy] = Field(default=None,
                                                          description=("The chunking strategy used to chunk the file(s). "
                                                                       "If not set, the auto strategy is used. "
                                                                       "Only applicable if file_ids is non-empty."))
    expires_after: Optional[ExpiresAfter] = Field(default=None,
                                                  description="The expiration policy for the vector store.")
    file_ids: Optional[List[str]] = Field(default=None,
                                          description="A list of File IDs that the vector store should use. "
                                                      "Useful for tools like file_search that can access files.")
    metadata: Optional[Dict[str, str]] = Field(default=None,
                                               description=("Set of up to 16 key-value pairs attached to the object. "
                                                            "Keys: max 64 characters. Values: max 512 characters."))
