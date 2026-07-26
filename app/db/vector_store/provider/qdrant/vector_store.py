# Qdrant components
from qdrant_client import AsyncQdrantClient
# LangChain Document
from langchain_core.documents import Document
# Other components
from typing import Any, ClassVar, Optional, List, Sequence, Literal, Union
# Qdrant components
from qdrant_client import models
# Embedding type
from uuid import uuid4
# Config
from app.core.config import DENSE_MODEL_NAME
# Contract
from app.db.vector_store.base import BaseAsyncVectorStore, Embedding
from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.db.vector_store.provider.qdrant.filter_translator import to_qdrant_filter
from app.schemas.vector_store.types import VectorStoreType
# Logger
from loggers import SystemLogger
# Other component
import asyncio


class AsyncQdrantVectorStore(BaseAsyncVectorStore):
    """
    An asynchronous vector store implementation using Qdrant for storing and querying document embeddings.
    This class provides methods to interact with Qdrant's vector database asynchronously,
    supporting operations like document insertion, similarity search, and collection management.
    """

    provider: ClassVar[VectorStoreType] = VectorStoreType.QDRANT

    def __init__(self,
                 collection_name: str,
                 client: AsyncQdrantClient,
                 distance: models.Distance = models.Distance.COSINE,
                 shard_number: int = 2,
                 quantization_mode: Literal["binary", "scalar", "product", "none"] = "scalar",
                 default_segment_number: int = 4,
                 on_disk: bool = True) -> None:
        """
        Initialize the AsyncQdrantVectorStore with the specified configuration.

        Args:
            collection_name: Name of the collection to store vectors in Qdrant.
            client: An instance of AsyncQdrantClient for database operations.
            distance: Distance metric to use for vector similarity. Default is COSINE.
            shard_number: Number of shards for distributed deployment. Default is 2.
            quantization_mode: Type of vector quantization to apply. Options are 'binary', 'scalar', 'product', or 'none'.
            default_segment_number: Number of segments to use for indexing. Affects performance characteristics.
            on_disk: If True, stores vectors on disk instead of RAM. Better for large collections.
        """
        # Init client
        self._client = client
        # Init params
        self._distance = distance
        self._shard_number = shard_number
        self._quantization_mode = quantization_mode
        self._default_segment_number = default_segment_number
        self._on_disk = on_disk
        self._collection_name = collection_name

    @property
    def collection_name(self) -> str:
        return self._collection_name

    # ------------------------------------------------------------------
    # COLLECTION LIFECYCLE
    # ------------------------------------------------------------------
    async def collection_exists(self) -> bool:
        """
        Check whether this collection already exists in Qdrant.

        Returns:
            bool: True if the collection exists.
        """
        return await self._client.collection_exists(self._collection_name)

    async def ensure_collection(self, embedding_dim: int) -> bool:
        """
        Create the collection if it is missing, tolerating concurrent creation.

        Callers run this once before inserting, so concurrent inserts never race
        to create the collection themselves. Between processes the check-then-act
        below is still racy by nature, so a failed create is re-checked: if the
        collection is there afterwards, another worker won the race and that is a
        success, not an error.

        Args:
            embedding_dim: Dimensionality of the vectors that will be stored.

        Returns:
            bool: True if this call created the collection, False if it already existed.

        Raises:
            Exception: If creation failed for a reason other than losing the race.
        """
        if await self.collection_exists():
            return False

        dense_vectors_config = self._get_dense_embedding_config(embedding_dimension = embedding_dim,
                                                                distance = self._distance,
                                                                on_disk = self._on_disk,
                                                                model = DENSE_MODEL_NAME)
        quantization_config = self._get_quantization_config(quantization_mode = self._quantization_mode,
                                                            always_ram = True)

        # Optimizer config
        # When indexing threshold is 0, It will enable to avoid unnecessary indexing of vectors,
        # which will be overwritten by the next batch.
        optimizers_config = models.OptimizersConfigDiff(default_segment_number = self._default_segment_number,
                                                        indexing_threshold = 0)

        try:
            await self._client.create_collection(collection_name = self._collection_name,
                                                 vectors_config = dense_vectors_config,
                                                 shard_number = self._shard_number,
                                                 quantization_config = quantization_config,
                                                 optimizers_config = optimizers_config)
        except Exception:
            # Another process may have created it in the gap since our check
            if await self.collection_exists():
                SystemLogger.debug(f"Collection {self._collection_name} created concurrently by another worker")
                return False
            raise

        # Restore a normal indexing threshold now that the bulk load can begin
        await self._client.update_collection(collection_name = self._collection_name,
                                             optimizer_config = models.OptimizersConfigDiff(indexing_threshold = 20000))
        return True

    async def delete_collection(self) -> bool:
        """
        Delete the collection associated with this vector store instance.

        Returns:
            bool: True if the collection was successfully deleted, False if it didn't exist.

        Raises:
            Exception: If there's an error during the deletion process.
        """
        try:
            # Check if collection exists
            exists = await self.collection_exists()
            if not exists:
                return False

            # Delete the collection
            await self._client.delete_collection(self._collection_name)
            return True
        except Exception as e:
            raise Exception(f"Failed to delete collection {self._collection_name}: {str(e)}")

    @staticmethod
    def _convert_documents_to_payloads(documents: Sequence[Document]) -> list[dict[str, Any]]:
        """
        Construct Qdrant payloads from LangChain Document objects.

        Args:
            documents (Sequence[Document]): LangChain documents

        Returns:
            list[dict]: Payloads ready for Qdrant insertion
        """
        if not documents:
            raise ValueError("Documents list is empty")

        payloads: list[dict[str, Any]] = []

        for doc in documents:
            payloads.append({
                "page_content": doc.page_content,
                "metadata": doc.metadata or {},
                "_node_type": "Document",
            })

        return payloads

    @staticmethod
    def _get_dense_embedding_config(embedding_dimension: int,
                                   model: str,
                                   distance: models.Distance,
                                   on_disk: bool,
                                   datatype: models.Datatype = models.Datatype.FLOAT16) -> dict[str, models.VectorParams]:
        """
        Generate configuration for dense vector storage in Qdrant.

        Args:
            embedding_dimension: Dimensionality of the embedding vectors.
            model: Name of the embedding model used.
            distance: Distance metric for vector comparison.
            on_disk: Whether to store vectors on disk.
            datatype: Data type for vector storage (default: FLOAT16).

        Returns:
            dict: Configuration dictionary for Qdrant vector storage.
        """
        # Configure HNSW (Hierarchical Navigable Small World) index for approximate nearest neighbor search
        hnsw_config = models.HnswConfigDiff(
            on_disk=on_disk,  # Store index on disk if specified
            # Note: HNSW parameters like 'm' (number of bi-directional links) and 'ef_construct' (search scope)
            # can be tuned here for performance optimization if needed
        )

        # Define vector storage parameters
        dense_vectors_config = models.VectorParams(
            size=embedding_dimension,  # Dimensionality of the vectors
            distance=distance,         # Distance metric (e.g., COSINE, EUCLID, DOT)
            on_disk=on_disk,           # Storage location preference
            hnsw_config=hnsw_config,   # Indexing configuration
            datatype=datatype          # Storage data type (FLOAT16 for memory efficiency)
        )

        # Return configuration mapped to the model name
        # This allows multiple embedding models to be used in the same collection
        return {model: dense_vectors_config}

    @staticmethod
    def _get_quantization_config(quantization_mode: Literal['binary', 'scalar', 'product', 'none'] = "scalar",
                                 always_ram: bool = True) -> Union[models.ScalarQuantization, models.BinaryQuantization, models.ProductQuantization]:
        """
        Get quantization config with mode
        :param quantization_mode: Include scalar, binary and product.
        :param always_ram: Indicated that quantized vectors is persisted on RAM.
        :return:
        """
        # Define quantization mode if enable
        if quantization_mode == "scalar":
            # Scalar mode, currently Qdrant only support INT8
            quantization_config = models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    # if specify 0.99, 1% of extreme values will be excluded from the quantization bounds.
                    always_ram=always_ram
                )
            )
        elif quantization_mode == "binary":
            # Binary mode
            quantization_config = models.BinaryQuantization(
                binary=models.BinaryQuantizationConfig(
                    always_ram=always_ram,
                ),
            )
        else:
            # Product quantization mode
            quantization_config = models.ProductQuantization(
                product=models.ProductQuantizationConfig(
                    compression=models.CompressionRatio.X16,  # Default X16
                    always_ram=always_ram,
                ),
            )
        return quantization_config

    # ------------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------------
    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Sequence[Embedding],
                               batch_size: int = 16) -> int:
        """
        Upsert documents and their vectors into an existing collection.

        This does not create the collection - call ensure_collection() once
        beforehand, which lets any number of these calls run in parallel.

        Args:
            documents: Documents to store, one per embedding.
            embeddings: Pre-computed vectors, aligned with documents.
            batch_size: Points per upsert round-trip.

        Returns:
            int: Number of documents written.

        Raises:
            ValueError: If documents is empty or lengths do not match.
        """
        if not documents:
            raise ValueError("Documents list is empty")
        if len(documents) != len(embeddings):
            raise ValueError(f"Number of documents ({len(documents)}) must equal "
                             f"number of embeddings ({len(embeddings)})")

        # Define payload
        payloads = self._convert_documents_to_payloads(documents)

        # Define points
        points = [models.PointStruct(id = str(uuid4()),
                                     vector = {DENSE_MODEL_NAME: embeddings[i]},
                                     payload = payloads[i]) for i in range(len(payloads))]

        # Batch upserts to bound request size instead of one giant call
        for batch_start in range(0, len(points), batch_size):
            batch = points[batch_start:batch_start + batch_size]
            await self._client.upsert(collection_name = self._collection_name,
                                      points = batch)
        return len(points)

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------
    async def retrieve(self,
                       query_vectors: Sequence[Embedding],
                       limit: int = 10,
                       filters: Optional[VectorStoreFilter] = None,
                       score_threshold: Optional[float] = None) -> List[List[RetrievedChunk]]:
        """
        Search for similar vectors in the collection.

        Args:
            query_vectors: One vector per query.
            limit: Maximum number of results to return per query.
            filters: Optional backend-neutral metadata filter.
            score_threshold: Minimum score threshold for results.

        Returns:
            List[List[RetrievedChunk]]: Hits per query, in query order.
        """
        query_filter = to_qdrant_filter(filters)

        # Define task
        tasks = [self._client.query_points(collection_name=self._collection_name,
                                           query=vector,
                                           using=DENSE_MODEL_NAME,
                                           limit=limit,
                                           score_threshold=score_threshold,
                                           query_filter=query_filter,
                                           with_payload=True,
                                           with_vectors=False) for vector in query_vectors]
        # Handle multiple at once
        responses = await asyncio.gather(*tasks)
        # Normalise into backend-neutral chunks
        return [self._to_retrieved_chunks(response) for response in responses]

    @staticmethod
    def _to_retrieved_chunks(response: models.QueryResponse) -> List[RetrievedChunk]:
        """Convert one Qdrant query response into backend-neutral chunks."""
        chunks: List[RetrievedChunk] = []
        for point in response.points:
            payload = point.payload or {}
            chunks.append(RetrievedChunk(id = str(point.id),
                                         score = point.score,
                                         content = payload.get("page_content", ""),
                                         metadata = payload.get("metadata", {}) or {}))
        return chunks