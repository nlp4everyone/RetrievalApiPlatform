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
from app.core.config import DENSE_MODEL_NAME, HYBRID_PREFETCH_MULTIPLIER, RRF_K, SPARSE_MODEL_NAME
# Contract
from app.db.vector_store.base import BaseAsyncVectorStore, Embedding, SparseEmbedding
from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.db.vector_store.provider.qdrant.filter_translator import to_qdrant_filter
from app.schemas.vector_store.types import VectorStoreType
# Logger
from loggers import SystemLogger
# Other component
import asyncio


def _vector_name(model: str) -> str:
    """
    Fold a model id into a name Qdrant accepts for a vector field.

    Qdrant rejects "/" and ":" in vector names - they separate segments in its
    own storage paths - and every model id carries a "/" (Qwen/Qwen3-Embedding-0.6B).
    The name is only a key inside the collection, so folding those two characters
    to "_" keeps the field recognisable as the model it came from at no cost.

    Args:
        model: Model id from settings.

    Returns:
        str: The same name with the characters Qdrant forbids replaced.
    """
    return model.replace("/", "_").replace(":", "_")


# Resolved once: these name the vector fields of every collection this store
# creates, and a rename would strand the collections created before it.
DENSE_VECTOR_NAME = _vector_name(DENSE_MODEL_NAME)
SPARSE_VECTOR_NAME = _vector_name(SPARSE_MODEL_NAME)


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
                 on_disk: bool = True,
                 hybrid_prefetch_multiplier: int = HYBRID_PREFETCH_MULTIPLIER,
                 rrf_k: Optional[int] = RRF_K) -> None:
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
            hybrid_prefetch_multiplier: Candidates each hybrid branch fetches, as a multiple of
                the requested limit. Defaults to retrieval.hybrid_prefetch_multiplier in config.yaml;
                pass it explicitly to sweep the value in an eval harness.
            rrf_k: RRF k for hybrid fusion, from retrieval.rrf_k. None leaves Qdrant's own
                default (60) and sends the same request shape as before k was tunable.
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
        self._hybrid_prefetch_multiplier = hybrid_prefetch_multiplier
        self._rrf_k = rrf_k

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def hybrid_prefetch_multiplier(self) -> int:
        """Prefetch depth per hybrid branch, so traces can record what produced a ranking."""
        return self._hybrid_prefetch_multiplier

    @property
    def rrf_k(self) -> Optional[int]:
        """RRF k in force, or None when Qdrant's default is left alone."""
        return self._rrf_k

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

    async def ensure_collection(self,
                                embedding_dim: int,
                                with_sparse: bool = False) -> bool:
        """
        Create the collection if it is missing, tolerating concurrent creation.

        Callers run this once before inserting, so concurrent inserts never race
        to create the collection themselves. Between processes the check-then-act
        below is still racy by nature, so a failed create is re-checked: if the
        collection is there afterwards, another worker won the race and that is a
        success, not an error.

        Args:
            embedding_dim: Dimensionality of the vectors that will be stored.
            with_sparse: Also create a named sparse vector field, so points can
                carry a dense and a sparse vector at once. Ignored when the
                collection already exists - Qdrant cannot add a vector field to
                a live collection, so an older dense-only store stays dense-only
                (see supports_sparse).

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
                                                                model = DENSE_VECTOR_NAME)
        sparse_vectors_config = (self._get_sparse_embedding_config(model = SPARSE_VECTOR_NAME,
                                                                   on_disk = self._on_disk)
                                 if with_sparse else None)
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
                                                 sparse_vectors_config = sparse_vectors_config,
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

    async def supports_sparse(self) -> bool:
        """
        Whether this collection was created with a sparse vector field.

        Read from the live collection rather than from config: sparse embedding
        can be switched on long after a store was created, and Qdrant cannot add
        a vector field afterwards. Upserting a sparse vector into a collection
        without the field fails the whole batch, so writers check here first.

        Returns:
            bool: True if the collection exists and holds the SPARSE_VECTOR_NAME
                sparse vector field.
        """
        if not await self.collection_exists():
            return False
        info = await self._client.get_collection(self._collection_name)
        sparse_vectors = info.config.params.sparse_vectors or {}
        return SPARSE_VECTOR_NAME in sparse_vectors

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
    def _get_sparse_embedding_config(model: str,
                                     on_disk: bool) -> dict[str, models.SparseVectorParams]:
        """
        Generate configuration for sparse vector storage in Qdrant.

        Sparse vectors get an inverted index instead of HNSW, and no
        quantization - there is nothing to compress in a {token_id: weight}
        mapping the way there is in a dense float array. No `modifier` is set:
        BGE-M3 already emits learned term weights, so applying IDF on top would
        re-weight scores that are not raw frequencies.

        Args:
            model: Name of the sparse embedding model, used as the field name.
            on_disk: Whether to keep the inverted index on disk.

        Returns:
            dict: Sparse vector configuration, keyed by model name so it sits
                beside the dense field rather than replacing it.
        """
        return {model: models.SparseVectorParams(index = models.SparseIndexParams(on_disk = on_disk))}

    @staticmethod
    def _get_quantization_config(quantization_mode: Literal['binary', 'scalar', 'product', 'none'] = "scalar",
                                 always_ram: bool = True) -> Optional[Union[models.ScalarQuantization,
                                                                            models.BinaryQuantization,
                                                                            models.ProductQuantization]]:
        """
        Build the quantization config for a new collection.

        Args:
            quantization_mode: One of "scalar", "binary", "product", or "none"
                to store vectors unquantized.
            always_ram: Keep quantized vectors in RAM.

        Returns:
            The matching quantization config, or None for "none" - which Qdrant
            reads as "do not quantize".

        Raises:
            ValueError: If the mode is not one of the four accepted values.
        """
        if quantization_mode == "none":
            return None

        if quantization_mode == "scalar":
            # Scalar mode, currently Qdrant only support INT8
            return models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    # if specify 0.99, 1% of extreme values will be excluded from the quantization bounds.
                    always_ram=always_ram
                )
            )

        if quantization_mode == "binary":
            return models.BinaryQuantization(
                binary=models.BinaryQuantizationConfig(
                    always_ram=always_ram,
                ),
            )

        if quantization_mode == "product":
            return models.ProductQuantization(
                product=models.ProductQuantizationConfig(
                    compression=models.CompressionRatio.X16,  # Default X16
                    always_ram=always_ram,
                ),
            )

        raise ValueError(f"Unsupported quantization_mode: {quantization_mode!r}")

    # ------------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------------
    async def insert_documents(self,
                               documents: Sequence[Document],
                               embeddings: Sequence[Embedding],
                               batch_size: int = 16,
                               sparse_embeddings: Optional[Sequence[SparseEmbedding]] = None) -> int:
        """
        Upsert documents and their vectors into an existing collection.

        This does not create the collection - call ensure_collection() once
        beforehand, which lets any number of these calls run in parallel.

        Args:
            documents: Documents to store, one per embedding.
            embeddings: Pre-computed dense vectors, aligned with documents.
            batch_size: Points per upsert round-trip.
            sparse_embeddings: Optional {token_id: weight} mappings, aligned with
                documents. Each point then carries a dense and a sparse vector
                under their model names; the collection must have been created
                with with_sparse (see supports_sparse).

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
        if sparse_embeddings is not None and len(documents) != len(sparse_embeddings):
            raise ValueError(f"Number of documents ({len(documents)}) must equal "
                             f"number of sparse embeddings ({len(sparse_embeddings)})")

        # Define payload
        payloads = self._convert_documents_to_payloads(documents)

        # Define points
        points = [models.PointStruct(id = str(uuid4()),
                                     vector = self._to_point_vector(
                                         dense = embeddings[i],
                                         sparse = sparse_embeddings[i] if sparse_embeddings is not None else None),
                                     payload = payloads[i]) for i in range(len(payloads))]

        # Batch upserts to bound request size instead of one giant call
        for batch_start in range(0, len(points), batch_size):
            batch = points[batch_start:batch_start + batch_size]
            await self._client.upsert(collection_name = self._collection_name,
                                      points = batch)
        return len(points)

    @staticmethod
    def _to_point_vector(dense: Embedding,
                         sparse: Optional[SparseEmbedding] = None) -> dict[str, Any]:
        """
        Build one point's named-vector mapping.

        Both representations are keyed by their model name, so a hybrid point is
        the dense point plus one more entry - nothing about the dense field
        changes, and a dense-only reader keeps working unchanged.

        Args:
            dense: Dense vector for this point.
            sparse: Optional {token_id: weight} mapping for the same point.

        Returns:
            dict: Named vectors ready for models.PointStruct.
        """
        vector: dict[str, Any] = {DENSE_VECTOR_NAME: dense}
        if sparse:
            # Qdrant wants two parallel arrays, not a mapping
            vector[SPARSE_VECTOR_NAME] = models.SparseVector(indices = list(sparse.keys()),
                                                            values = list(sparse.values()))
        return vector

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------
    async def retrieve(self,
                       query_vectors: Sequence[Embedding],
                       limit: int = 10,
                       filters: Optional[VectorStoreFilter] = None,
                       score_threshold: Optional[float] = None,
                       sparse_query_vectors: Optional[Sequence[SparseEmbedding]] = None) -> List[List[RetrievedChunk]]:
        """
        Search for similar vectors in the collection.

        With sparse vectors supplied this becomes a hybrid search: Qdrant runs
        the dense and the sparse branch as prefetches and fuses them with RRF
        server-side, so it stays one round-trip and the two incomparable score
        scales are never mixed - only their ranks are.

        Args:
            query_vectors: One dense vector per query.
            limit: Maximum number of results to return per query.
            filters: Optional backend-neutral metadata filter.
            score_threshold: Minimum score threshold, in dense-score scale.
            sparse_query_vectors: Optional sparse vector per query, aligned with
                query_vectors. The collection must hold a sparse field.

        Returns:
            List[List[RetrievedChunk]]: Hits per query, in query order.

        Raises:
            ValueError: If sparse vectors are given but do not match the dense ones in number.
        """
        if sparse_query_vectors is not None and len(sparse_query_vectors) != len(query_vectors):
            raise ValueError(f"Number of dense query vectors ({len(query_vectors)}) must equal "
                             f"number of sparse query vectors ({len(sparse_query_vectors)})")

        query_filter = to_qdrant_filter(filters)

        # Define task
        tasks = [self._build_query(dense_vector = vector,
                                   sparse_vector = sparse_query_vectors[i] if sparse_query_vectors else None,
                                   limit = limit,
                                   query_filter = query_filter,
                                   score_threshold = score_threshold)
                 for i, vector in enumerate(query_vectors)]
        # Handle multiple at once
        responses = await asyncio.gather(*tasks)
        # Normalise into backend-neutral chunks
        return [self._to_retrieved_chunks(response) for response in responses]

    def _build_query(self,
                     dense_vector: Embedding,
                     sparse_vector: Optional[SparseEmbedding],
                     limit: int,
                     query_filter: Optional[models.Filter],
                     score_threshold: Optional[float]) -> Any:
        """
        Issue one query - dense-only, or dense+sparse fused by Qdrant.

        The hybrid form asks each branch for more than `limit` candidates
        (hybrid_prefetch_multiplier): fusion can only reorder what it is given,
        so a document ranked just outside the dense top-k but first on the
        sparse side has to be in the pool to be able to win.

        score_threshold rides on the dense prefetch rather than the fused query,
        because it is expressed in similarity scale - applied to RRF output it
        would compare against ~1/(60+rank) values and drop everything.

        Args:
            dense_vector: Dense query vector.
            sparse_vector: Optional sparse query vector; None means dense-only.
            limit: Maximum hits to return.
            query_filter: Translated metadata filter, or None.
            score_threshold: Minimum dense score, or None.

        Returns:
            Awaitable resolving to the Qdrant query response.
        """
        if not sparse_vector:
            return self._client.query_points(collection_name = self._collection_name,
                                             query = dense_vector,
                                             using = DENSE_VECTOR_NAME,
                                             limit = limit,
                                             score_threshold = score_threshold,
                                             query_filter = query_filter,
                                             with_payload = True,
                                             with_vectors = False)

        prefetch_limit = limit * self._hybrid_prefetch_multiplier
        prefetch = [
            models.Prefetch(query = dense_vector,
                            using = DENSE_VECTOR_NAME,
                            limit = prefetch_limit,
                            filter = query_filter,
                            score_threshold = score_threshold),
            models.Prefetch(query = models.SparseVector(indices = list(sparse_vector.keys()),
                                                        values = list(sparse_vector.values())),
                            using = SPARSE_VECTOR_NAME,
                            limit = prefetch_limit,
                            filter = query_filter),
        ]
        # Left at None the request is byte-identical to the one sent before k was
        # tunable, so the default path cannot regress on an older server
        fusion_query = (models.FusionQuery(fusion = models.Fusion.RRF) if self._rrf_k is None
                        else models.RrfQuery(rrf = models.Rrf(k = self._rrf_k)))

        return self._client.query_points(collection_name = self._collection_name,
                                         prefetch = prefetch,
                                         query = fusion_query,
                                         limit = limit,
                                         query_filter = query_filter,
                                         with_payload = True,
                                         with_vectors = False)

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