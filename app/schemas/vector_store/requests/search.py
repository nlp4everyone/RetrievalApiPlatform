from typing import Optional, Union, List, Any, Literal
from pydantic import BaseModel, Field
from enum import Enum

from app.schemas.vector_store.types import SearchType

class ComparisonType(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NIN = "nin"

class ComparisonFilter(BaseModel):
    """A filter used to compare a specified attribute key to a given value."""
    key: str = Field(..., description="The key to compare against the value.")
    type: ComparisonType = Field(..., description="The comparison operator to use.")
    value: Union[str, int, float, bool, List[Any]] = Field(...,
                                                           description="The value to compare against the attribute key.")

class CompoundFilterType(str, Enum):
    AND = "and"
    OR = "or"

class CompoundFilter(BaseModel):
    """Combine multiple filters using AND or OR."""
    type: CompoundFilterType = Field(..., description="Type of operation: 'and' or 'or'.")
    filters: List[Union['ComparisonFilter', 'CompoundFilter']] = Field(...,
                                                                       description="Array of filters to combine.")

# Update forward refs for recursive types
ComparisonFilter.update_forward_refs()
CompoundFilter.update_forward_refs()

class RankingOptions(BaseModel):
    """Ranking options for search."""
    ranker: Literal["auto", "none"] = Field(
        default="auto", 
        description="Enable re-ranking; set to 'none' to disable, which can help reduce latency."
    )
    score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum score threshold for results."
    )
    rewrite_query: bool = Field(
        default=False,
        description="Whether to rewrite the natural language query for vector search."
    )

class VectorStoreSearchRequest(BaseModel):
    """Request model for searching a vector store."""
    query: Union[str, List[str]] = Field(
        ...,
        description="A query string or list of query strings for the search."
    )
    filters: Optional[Union[ComparisonFilter, CompoundFilter]] = Field(
        default=None,
        description="Optional filter to apply based on document attributes."
    )
    max_num_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="The maximum number of results to return (1-50)."
    )
    ranking_options: Optional[RankingOptions] = Field(
        default=None,
        description="Options for controlling search result ranking."
    )
    search_type: Literal["auto", "dense", "hybrid"] = Field(
        default="auto",
        description="How the query is answered. 'auto' resolves per collection: "
                    "hybrid when sparse vectors are available for it, dense "
                    "otherwise. 'hybrid' is rejected when the collection cannot "
                    "answer it, rather than silently falling back to dense. "
                    "Not part of the OpenAI schema - the official SDK sends it "
                    "via extra_body={'search_type': ...}."
    )

    @property
    def requested_search_type(self) -> Optional[SearchType]:
        """The search type this request pins, if any.

        Returns:
            Optional[SearchType]: The pinned search type, or None for 'auto',
                which leaves the choice to resolve_search_type()
        """
        if self.search_type == "auto":
            return None
        return SearchType(self.search_type)
