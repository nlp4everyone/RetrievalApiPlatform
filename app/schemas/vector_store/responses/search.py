from typing import List, Optional, Dict, Any, Literal, Union
from pydantic import BaseModel, Field

class ContentChunk(BaseModel):
    """Represents a single content chunk in the search results."""
    type: Literal["text"] = "text"
    text: str

class SearchResult(BaseModel):
    """Represents a single search result item with its metadata and content."""
    file_id: Optional[str] = Field(None, description="Unique identifier for the file")
    filename: Optional[str] = Field(None, description="Name of the file")
    score: float = Field(..., ge=0.0, le=1.0, description="Relevance score of the search result")
    attributes: Optional[Dict[str, Any]] = Field(None,
                                                 description="Additional metadata attributes for the file")
    content: List[ContentChunk] = Field(
        ...,
        description="List of content chunks from the file"
    )

class VectorStoreSearchResponse(BaseModel):
    """Paginated response for vector store search results."""
    object: Literal["vector_store.search_results.page"] = "vector_store.search_results.page"
    search_query: Union[str,List[str]] = Field(..., description="The search query that produced these results")
    data: List[SearchResult] = Field(
        default_factory=list,
        description="List of search result items"
    )
    has_more: bool = Field(
        default=False,
        description="Whether there are more results available"
    )
    next_page: Optional[str] = Field(
        None,
        description="Cursor for the next page of results, if available"
    )
