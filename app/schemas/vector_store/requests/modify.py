# Typing
from typing import Dict, Optional
from pydantic import Field, BaseModel

class VectorStoreModifyRequest(BaseModel):
    """Request model for modifying a vector store."""
    name: Optional[str] = Field(default=None,
                                description="The name of the vector store.")
    metadata: Optional[Dict[str, str]] = Field(default=None,
                                               description=("Set of up to 16 key-value pairs attached to the object. "
                                                            "Keys: max 64 characters. Values: max 512 characters."))
