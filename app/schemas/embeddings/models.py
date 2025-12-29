# Typing
from typing import  List, Literal, Union, Optional
from pydantic import Field
# Dependencies
from ..base.common import BaseModel

class EmbeddingUsage(BaseModel):
    """Usage statistics for embedding generation"""
    prompt_tokens: int = Field(...,
                               description="Number of prompt tokens used over the course of the run")
    total_tokens: int = Field(...,
                              description="Total number of tokens used (prompt + completion)")

class EmbeddingObject(BaseModel):
    """Single embedding vector with metadata"""
    object: Literal["embedding"] = Field("embedding",
                                         description="The object type, which is always 'embedding'")
    index: int = Field(...,
                       description="The index of the embedding in the list of embeddings")
    embedding: Union[List[float], str] = Field(...,
                                               description="The embedding vector, which is a list of floats. "
                                                           "The length of vector depends on the model as listed in the embedding guide.")

class ListEmbeddingObject(BaseModel):
    """Complete embedding response object"""
    object: Literal["list"] = "list"
    model: Optional[str] = Field(default=None,
                                 description="Filter by model ID")
    data: List[EmbeddingObject]
    usage: Optional[EmbeddingUsage] = Field(None,
                                            description="Usage statistics related to the run. "
                                                        "This value will be null if the run is not in a terminal state")
