"""Retrieval backends: dense-only, or dense + sparse fused by the vector store."""
from .base import BaseRetriever, RetrievalQuery
from .dense_retriever import DenseRetriever
from .hybrid_retriever import HybridRetriever