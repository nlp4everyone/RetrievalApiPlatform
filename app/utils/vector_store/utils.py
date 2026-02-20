# Typing
from typing import List
# Schema imports
from app.schemas.vector_store.responses.search import SearchResult, ContentChunk
# Exception imports
from app.exceptions.vector_store import WrongPrefixVectorstoreException
# Qdrant client imports
from qdrant_client import models


def convert_query_response_to_search_results(retrieved_results: List[models.QueryResponse]) -> List[SearchResult]:
    """
    Convert Qdrant QueryResponse objects to standardized SearchResult objects.
    
    This function transforms raw Qdrant search results into the application's
    standardized SearchResult format, extracting metadata, content, and scores
    while cleaning up the content for better readability.
    
    Args:
        retrieved_results: List of Qdrant QueryResponse objects containing
                          the raw search results from the vector database
        
    Returns:
        List[SearchResult]: List of standardized SearchResult objects with
                           cleaned content and extracted metadata

    """
    documents = []
    
    # Process each query response (may contain multiple points)
    for query_result in retrieved_results:
        # Process each point (search result) within the query response
        for point in query_result.points:
            # Extract payload, defaulting to empty dict if missing
            payload = point.payload or {}
            metadata = payload.get('metadata', {})
            content = payload.get('page_content', '')

            # Clean up the content by removing extra whitespace and newlines
            # This improves readability and removes formatting artifacts
            cleaned_content = ' '.join(
                line.strip()
                for line in content.splitlines()
                if line.strip()
            )

            # Create standardized SearchResult object
            documents.append(
                SearchResult(
                    score=point.score,
                    attributes=metadata,
                    content=[ContentChunk(text=cleaned_content)]
                )
            )

    return documents


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
