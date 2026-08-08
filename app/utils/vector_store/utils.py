# Typing
from typing import List, Optional, Union
# Schema imports
from app.schemas.vector_store.requests.search import (ComparisonFilter,
                                                      CompoundFilter,
                                                      CompoundFilterType)
from app.schemas.vector_store.responses.search import SearchResult, ContentChunk
# Backend-neutral vector store types
from app.db.vector_store.types import (FieldCondition,
                                       FilterCombinator,
                                       FilterGroup,
                                       FilterOperator,
                                       RetrievedChunk,
                                       VectorStoreFilter)
# Exception imports
from app.exceptions.vector_store import WrongPrefixVectorstoreException


def convert_retrieved_chunks_to_search_results(retrieved_chunks: List[RetrievedChunk]) -> List[SearchResult]:
    """
    Convert backend-neutral retrieval hits into API search results.

    Works the same whichever vector store produced the hits - normalisation
    into RetrievedChunk happens inside the backend, so nothing here depends on
    Qdrant or Milvus.

    Args:
        retrieved_chunks: Hits returned by a vector store

    Returns:
        List[SearchResult]: Standardized results with cleaned content and metadata
    """
    documents = []

    for chunk in retrieved_chunks:
        # Clean up the content by removing extra whitespace and newlines
        # This improves readability and removes formatting artifacts
        cleaned_content = ' '.join(
            line.strip()
            for line in chunk.content.splitlines()
            if line.strip()
        )

        # Create standardized SearchResult object. Rounded only here, at the
        # response boundary - threshold and fusion rank on the full score.
        documents.append(
            SearchResult(
                score=round(chunk.score, 5),
                attributes=chunk.metadata,
                content=[ContentChunk(text=cleaned_content)]
            )
        )

    return documents


def normalize_search_filter(
    request_filter: Optional[Union[ComparisonFilter, CompoundFilter]]
) -> Optional[VectorStoreFilter]:
    """
    Convert an API filter into the backend-neutral filter the stores understand.

    Keeps the OpenAI-compatible request schema separate from what app.db
    consumes, so each backend translates from one shape rather than from the
    HTTP layer's models.

    Args:
        request_filter: Filter as it arrived on the search request, or None

    Returns:
        Optional[VectorStoreFilter]: Neutral filter tree, or None if nothing was given

    Raises:
        ValueError: If the filter node is of an unrecognised type
    """
    if request_filter is None:
        return None

    if isinstance(request_filter, ComparisonFilter):
        return FieldCondition(key=request_filter.key,
                              operator=FilterOperator(request_filter.type.value),
                              value=request_filter.value)

    if isinstance(request_filter, CompoundFilter):
        combinator = (FilterCombinator.AND
                      if request_filter.type == CompoundFilterType.AND
                      else FilterCombinator.OR)
        return FilterGroup(combinator=combinator,
                           conditions=[normalize_search_filter(child)
                                       for child in request_filter.filters])

    raise ValueError(f"Unsupported filter type: {type(request_filter).__name__}")


def validate_vector_store_prefix(vector_store_id: str) -> None:
    """
    Validate that a vector store ID has the correct prefix.

    This function checks if the provided vector store ID starts with the
    expected prefix 'vs' (vector store). If the prefix is incorrect,
    it raises a specific exception to help with debugging and API consistency.

    Args:
        vector_store_id: The vector store ID to validate

    Raises:
        WrongPrefixVectorstoreException: If the ID doesn't start with 'vs'
    """
    # Check if the vector store ID starts with the expected prefix
    if not vector_store_id.startswith("vs"):
        raise WrongPrefixVectorstoreException(input=vector_store_id)