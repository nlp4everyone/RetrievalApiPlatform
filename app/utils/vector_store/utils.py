# FastAPI component
from fastapi import HTTPException, status
# Typing
from typing import Union, List
# Schema
from app.schemas.vector_store.responses.search import SearchResult, ContentChunk
from app.schemas.vector_store.requests import *
# Qdrant component
from qdrant_client import models

def convert_query_response_to_search_results(retrieved_results: List[models.QueryResponse]) -> List[SearchResult]:
    """
    Convert Qdrant QueryResponse to a list of SearchResult objects.

    Args:
        retrieved_results: List of Qdrant SearchResult objects containing the search results

    Returns:
        List[SearchResult]: List of formatted search results
    """
    documents = []
    for query_result in retrieved_results:
        for point in query_result.points:
            payload = point.payload or {}
            metadata = payload.get('metadata', {})
            content = payload.get('page_content', '')

            # Clean up the content by removing extra whitespace and newlines
            cleaned_content = ' '.join(
                line.strip()
                for line in content.splitlines()
                if line.strip()
            )

            documents.append(
                SearchResult(
                    score=point.score,
                    attributes=metadata,
                    content=[ContentChunk(text=cleaned_content)]
                )
            )

    return documents

