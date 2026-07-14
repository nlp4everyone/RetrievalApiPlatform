# Qdrant components
from qdrant_client import AsyncQdrantClient
# LangChain Document
from langchain_core.documents import Document
# Other components
from typing import Optional, List, Sequence, Literal, Union
# Qdrant components
from qdrant_client import models
# Embedding type
from uuid import uuid4
# Config
from app.core.config import DENSE_MODEL_NAME
# Other component
import asyncio

# DataType
Embedding = List[float]

class AsyncQdrantVectorStore:
    """
    An asynchronous vector store implementation using Qdrant for storing and querying document embeddings.
    This class provides methods to interact with Qdrant's vector database asynchronously,
    supporting operations like document insertion, similarity search, and collection management.
    """
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

    async def _create_collection(self,
                                 dense_vectors_config: Union[models.VectorParams, dict],
                                 sparse_vectors_config: Optional[dict[str, models.SparseVectorParams]] = None,
                                 shard_number: int = 2,
                                 quantization_mode: Literal['binary', 'scalar', 'product', 'none'] = "scalar",
                                 default_segment_number: int = 4,
                                 always_ram: bool = True) -> None:
        """
        Create collection with default name

        Args:
            dense_vectors_config: Config for dense vector
            sparse_vectors_config: Config for sparse vector
            shard_number: The number of parallel processes as the same time. Default is 2.
            quantization_mode: Quantization mode.
            default_segment_number: Default is 4. Larger value will enhance the latency, smaller one the throughput.
            always_ram: Indicated that quantized vectors is persisted on RAM.
        """
        # When collection not existed!
        status = await self._client.collection_exists(self._collection_name)
        if not status:
            quantization_config = self._get_quantization_config(quantization_mode = quantization_mode,
                                                                always_ram = always_ram)

            # Optimizer config
            # When indexing threshold is 0, It will enable to avoid unnecessary indexing of vectors,
            # which will be overwritten by the next batch.
            optimizers_config = models.OptimizersConfigDiff(default_segment_number = default_segment_number,
                                                            indexing_threshold = 0)

            # Create collection
            await self._client.create_collection(collection_name = self._collection_name,
                                                 vectors_config = dense_vectors_config,
                                                 sparse_vectors_config = sparse_vectors_config,
                                                 shard_number = shard_number,
                                                 quantization_config = quantization_config,
                                                 optimizers_config = optimizers_config)
            # Update collection
            await self._client.update_collection(collection_name = self._collection_name,
                                                 optimizer_config = models.OptimizersConfigDiff(indexing_threshold=20000))
    
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
            exists = await self._client.collection_exists(self._collection_name)
            if not exists:
                return False
                
            # Delete the collection
            await self._client.delete_collection(self._collection_name)
            return True
        except Exception as e:
            raise Exception(f"Failed to delete collection {self._collection_name}: {str(e)}")

    @staticmethod
    def _convert_documents_to_payloads(documents: Sequence[Document]) -> list[dict]:
        """
        Construct Qdrant payloads from LangChain Document objects.

        Args:
            documents (Sequence[Document]): LangChain documents

        Returns:
            list[dict]: Payloads ready for Qdrant insertion
        """
        if not documents:
            raise ValueError("Documents list is empty")

        payloads: list[dict] = []

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
                                   datatype: models.Datatype = models.Datatype.FLOAT16) -> dict:
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
                                 always_ram: bool = True):
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
            ),
        else:
            # Product quantization mode
            quantization_config = models.ProductQuantization(
                product=models.ProductQuantizationConfig(
                    compression=models.CompressionRatio.X16,  # Default X16
                    always_ram=always_ram,
                ),
            )
        return quantization_config

    async def _insert_points(self,
                           dense_embeddings: list[list[float]],
                           payloads: list[dict],
                           embedding_model_name: str,
                           point_ids: Optional[list[str]] = None,
                           batch_size: int = 16,
                           parallel: int = 1) -> None:
        """
        Insert or update points (vectors with payloads) in the Qdrant collection.
        
        This method handles the creation of point structures from embeddings and payloads,
        and performs batch insertion into the Qdrant collection. If point_ids are not provided,
        they will be automatically generated as UUIDs.

        Args:
            dense_embeddings: List of embedding vectors to be stored. Each vector should be a list of floats.
            payloads: List of metadata dictionaries associated with each embedding.
                     Should match the length of dense_embeddings.
            embedding_model_name: Name of the embedding model used, which will be used as the key
                                in the vector storage.
            point_ids: Optional list of unique identifiers for each point. If not provided,
                     UUIDs will be automatically generated.
            batch_size: Number of points to process in each batch. Default is 16.
            parallel: Number of parallel operations to perform. Default is 1.
                     Note: This parameter is currently not used in the implementation.

        Raises:
            Exception: If the number of embeddings doesn't match the number of payloads.
        
        Note:
            - The method automatically handles batching of points for efficient insertion.
            - All vectors in a batch will be uploaded atomically.
        """

        # Check size
        if not len(dense_embeddings) == len(payloads):
            raise Exception("Number of embeddings must be equal with number of payloads")
        # When point not specify
        if point_ids == None: point_ids = [str(uuid4()) for i in range(len(dense_embeddings))]

        # Define point
        points = [models.PointStruct(id = point_ids[i],
                                     vector = {embedding_model_name: dense_embeddings[i]},
                                     payload = payloads[i]) for i in range(len(dense_embeddings))]

        # Upload points
        await self._client.upsert(collection_name = self._collection_name,
                                  points = points)

    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Optional[List[Embedding]],
                               upload_batch_size: int = 16,
                               upload_parallel: int = 1) -> None:
        """
        Insert documents with their embeddings into the vector store.

        This method handles the complete pipeline of preparing documents,
        converting them to Qdrant's format, and uploading them in batches.

        Args:
            documents: Sequence of LangChain Document objects to be inserted.
            embeddings: Pre-computed embeddings for the documents. If None, they must be
                      computed using the provided embedding model.
            upload_batch_size: Number of documents to upload in each batch to Qdrant.
            upload_parallel: Number of parallel upload operations to perform.

        Raises:
            ValueError: If documents list is empty or embeddings don't match documents.
        """
        if not documents:
            raise ValueError("Documents list is empty")
        # Get dims
        embedding_dim = len(embeddings[0])

        # Get dense config
        dense_vectors_config = self._get_dense_embedding_config(embedding_dimension = embedding_dim,
                                                                distance = self._distance,
                                                                on_disk = self._on_disk,
                                                                model = DENSE_MODEL_NAME)
        # Define payload
        payloads = self._convert_documents_to_payloads(documents)

        # Create collection with config
        await self._create_collection(dense_vectors_config = dense_vectors_config,
                                      shard_number = self._shard_number,
                                      quantization_mode = self._quantization_mode,
                                      default_segment_number = self._default_segment_number)
        # Insert points
        await self._insert_points(dense_embeddings = embeddings,
                                  payloads = payloads,
                                  batch_size = upload_batch_size,
                                  parallel = upload_parallel,
                                  embedding_model_name = DENSE_MODEL_NAME)

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------
    async def retrieve(self,
                       query_vectors: List[List[float]],
                       query_filter: Optional[models.Filter] = None,
                       limit: int = 10,
                       score_threshold: Optional[float] = None,
                       search_params: Optional[models.SearchParams] = None,
                       with_payload: bool = True,
                       with_vectors: bool = False) -> List[models.QueryResponse]:
        """
        Search for similar vectors in the collection.

        Args:
            query_vectors: A single query vector or a list of query vectors to search with.
            query_filter: Optional filter to apply to the search results.
            limit: Maximum number of results to return per query.
            score_threshold: Minimum score threshold for results.
            search_params: Additional search parameters.
            with_payload: Whether to include the payload in the results.
            with_vectors: Whether to include the vectors in the results.

        Returns:
            List[models.ScoredPoint]: List of search results as ScoredPoint objects.

        Raises:
            ValueError: If the collection does not exist or is empty.
        """
        # Define task
        tasks = [self._client.query_points(collection_name=self._collection_name,
                                           query=v,
                                           using=DENSE_MODEL_NAME,
                                           limit=limit,
                                           score_threshold=score_threshold,
                                           search_params=search_params,
                                           with_payload=with_payload,
                                           with_vectors=with_vectors) for v in query_vectors]
        # Handle multiple at once
        results = await asyncio.gather(*tasks)
        # Return
        return results
