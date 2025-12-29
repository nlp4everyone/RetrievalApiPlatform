# Typing
from typing import List, Literal, Optional, Union
from pydantic import Field
# Other components
from ..base.pagination import PaginationParams
from ..base.common import BaseModel

class EmbeddingCreateRequest(BaseModel):
    """Request model for creating embeddings"""
    model: str = Field(..., description="ID of the model to use")
    input: Union[str, List[str]] = Field(...,
                                         description="Input text to embed, encoded as a string or array of tokens. "
                                         "To embed multiple inputs in a single request, pass an array of strings or array of token arrays")
    user: Optional[str] = Field(default=None,
                                description="A unique identifier representing your end-user, which can help OpenAI to monitor and detect abuse.")
    encoding_format: Literal["float", "base64"] = Field(default="float",
                                                        description="The format to return the embeddings in. "
                                                                    "Can be either float or base64")

class EmbeddingQueryRequest(PaginationParams):
    """Request model for querying embeddings"""
    model: Optional[str] = Field(default=None,
                                 description="Filter by model ID")
    has_usage: Optional[bool] = Field(default=None,
                                      description="Filter by whether usage data is available")
