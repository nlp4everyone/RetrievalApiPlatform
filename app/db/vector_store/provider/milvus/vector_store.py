"""Milvus implementation of the vector store contract.

Kept payload-compatible with the Qdrant backend: every document is one row with
``page_content`` and a ``metadata`` JSON field, so the service layer sees the
same RetrievedChunk whichever backend answered.

Two things differ from Qdrant by nature of the engine, and are handled here so
callers never see them:

* Milvus only searches collections that are *loaded*, and a restarted server
  comes back with everything unloaded - so searches load-and-retry once.
* Milvus collection and field names allow only letters, digits and underscores,
  which neither a ``vs-<uuid>`` id nor a model id satisfies.
"""
import re
from typing import Any, Callable, ClassVar, Coroutine, Dict, List, Optional, Sequence
from uuid import uuid4

from langchain_core.documents import Document
from pymilvus import AnnSearchRequest, AsyncMilvusClient, DataType, RRFRanker
from pymilvus.exceptions import MilvusException

from app.core.config import HYBRID_PREFETCH_MULTIPLIER, RRF_K
from app.db.vector_store.base import BaseAsyncVectorStore, Embedding, SparseEmbedding
from app.db.vector_store.provider.milvus.filter_translator import to_milvus_expression
from app.db.vector_store.types import RetrievedChunk, VectorStoreFilter
from app.schemas.vector_store.types import VectorStoreType
from loggers import SystemLogger

# Field names are fixed rather than derived from the model id the way Qdrant's
# are: Milvus forbids the "/", "-" and "." every model id carries, and the field
# is only a key inside the collection.
ID_FIELD = "id"
CONTENT_FIELD = "page_content"
METADATA_FIELD = "metadata"
DENSE_VECTOR_NAME = "dense_vector"
SPARSE_VECTOR_NAME = "sparse_vector"

# Milvus' own ceiling for a VARCHAR field, so a chunk is only ever rejected for
# being longer than the engine can store at all
_MAX_CONTENT_LENGTH = 65535
# vs-<uuid4 hex> is 35 characters; 64 leaves room for a longer id scheme
_MAX_ID_LENGTH = 64

# Anything Milvus does not accept in a collection name
_ILLEGAL_NAME_CHARS = re.compile(r"[^0-9a-zA-Z_]")

# Cosine for dense, inner product for sparse - the only metric Milvus offers on
# a sparse field, and the right one for term-weight dot products
_DENSE_METRIC = "COSINE"
_SPARSE_METRIC = "IP"

# Milvus' default RRF k, matching Qdrant's, used when config leaves rrf_k unset
_DEFAULT_RRF_K = 60


def _collection_name(name: str) -> str:
    """
    Fold a vector store id into a name Milvus accepts for a collection.

    Vector store ids look like ``vs-a1b2...``, and Milvus rejects the hyphen -
    collection names may hold only letters, digits and underscores, and may not
    start with a digit. Ids are unique in their hyphenated form and the mapping
    only rewrites separators, so distinct ids stay distinct.

    Args:
        name: Logical collection name (the vector store id).

    Returns:
        str: The same name with the characters Milvus forbids replaced.
    """
    folded = _ILLEGAL_NAME_CHARS.sub("_", name)
    return folded if folded[:1].isalpha() or folded[:1] == "_" else f"_{folded}"


class AsyncMilvusVectorStore(BaseAsyncVectorStore):
    """Operations against a single Milvus collection."""

    provider: ClassVar[VectorStoreType] = VectorStoreType.MILVUS

    def __init__(self,
                 collection_name: str,
                 client: AsyncMilvusClient,
                 shard_number: int = 2,
                 consistency_level: str = "Strong",
                 hnsw_m: int = 16,
                 hnsw_ef_construction: int = 200,
                 hybrid_prefetch_multiplier: int = HYBRID_PREFETCH_MULTIPLIER,
                 rrf_k: Optional[int] = RRF_K) -> None:
        """
        Args:
            collection_name: Collection to operate on (the vector store id).
            client: Connected Milvus client supplied by MilvusConnection.
            shard_number: Shards for a new collection, matching the Qdrant backend.
            consistency_level: Milvus read guarantee. "Strong" so a search sees
                what ingestion just wrote, which is what Qdrant does and what a
                caller polling a vector store into "completed" then searching it
                expects. Relax it to "Bounded" to trade freshness for latency.
            hnsw_m: Bi-directional links per HNSW node.
            hnsw_ef_construction: Candidate list size while building the index.
            hybrid_prefetch_multiplier: Candidates each hybrid branch fetches, as
                a multiple of the requested limit. Defaults to
                retrieval.hybrid_prefetch_multiplier in config.yaml.
            rrf_k: RRF k for hybrid fusion, from retrieval.rrf_k. None leaves
                Milvus' own default (60), the same value Qdrant defaults to.
        """
        self._client = client
        self._collection_name = collection_name
        self._physical_name = _collection_name(collection_name)
        self._shard_number = shard_number
        self._consistency_level = consistency_level
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construction = hnsw_ef_construction
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
        """RRF k in force, or None when Milvus' default is left alone."""
        return self._rrf_k

    # ------------------------------------------------------------------
    # COLLECTION LIFECYCLE
    # ------------------------------------------------------------------
    async def collection_exists(self) -> bool:
        """
        Check whether this collection already exists in Milvus.

        Returns:
            bool: True if the collection exists.
        """
        return await self._client.has_collection(self._physical_name)

    async def ensure_collection(self,
                                embedding_dim: int,
                                with_sparse: bool = False) -> bool:
        """
        Create the collection if it is missing, tolerating concurrent creation.

        Args:
            embedding_dim: Dimensionality of the vectors that will be stored.
            with_sparse: Also create a sparse vector field, so rows can carry a
                dense and a sparse vector at once. Ignored when the collection
                already exists - Milvus keeps the schema it was created with
                (see supports_sparse).

        Returns:
            bool: True if this call created the collection, False if it existed.

        Raises:
            Exception: If creation failed for a reason other than losing the race.
        """
        if await self.collection_exists():
            return False

        schema = self._client.create_schema(auto_id = False,
                                            enable_dynamic_field = False)
        schema.add_field(ID_FIELD, DataType.VARCHAR, is_primary = True, max_length = _MAX_ID_LENGTH)
        schema.add_field(CONTENT_FIELD, DataType.VARCHAR, max_length = _MAX_CONTENT_LENGTH)
        # Nullable so a document with no metadata is still a valid row
        schema.add_field(METADATA_FIELD, DataType.JSON, nullable = True)
        schema.add_field(DENSE_VECTOR_NAME, DataType.FLOAT_VECTOR, dim = embedding_dim)
        if with_sparse:
            schema.add_field(SPARSE_VECTOR_NAME, DataType.SPARSE_FLOAT_VECTOR)

        index_params = self._client.prepare_index_params()
        index_params.add_index(field_name = DENSE_VECTOR_NAME,
                               index_type = "HNSW",
                               metric_type = _DENSE_METRIC,
                               params = {"M": self._hnsw_m,
                                         "efConstruction": self._hnsw_ef_construction})
        if with_sparse:
            # An inverted index, not HNSW: there is nothing to navigate in a
            # {token_id: weight} mapping
            index_params.add_index(field_name = SPARSE_VECTOR_NAME,
                                   index_type = "SPARSE_INVERTED_INDEX",
                                   metric_type = _SPARSE_METRIC)

        try:
            # Passing index_params also loads the collection, so it is
            # searchable as soon as this returns
            await self._client.create_collection(collection_name = self._physical_name,
                                                 schema = schema,
                                                 index_params = index_params,
                                                 num_shards = self._shard_number,
                                                 consistency_level = self._consistency_level)
        except Exception:
            # Another process may have created it in the gap since our check
            if await self.collection_exists():
                SystemLogger.debug(f"Collection {self._physical_name} created concurrently by another worker")
                return False
            raise

        return True

    async def supports_sparse(self) -> bool:
        """
        Whether this collection was created with a sparse vector field.

        Read from the live collection rather than from config: sparse embedding
        can be switched on long after a store was created, and Milvus keeps the
        schema the collection was created with.

        Returns:
            bool: True if the collection exists and holds a sparse vector field.
        """
        if not await self.collection_exists():
            return False
        description = await self._client.describe_collection(self._physical_name)
        return any(field.get("name") == SPARSE_VECTOR_NAME
                   for field in description.get("fields", []))

    async def delete_collection(self) -> bool:
        """
        Drop the collection this instance is bound to.

        Returns:
            bool: True if a collection was deleted, False if it did not exist.

        Raises:
            Exception: If there's an error during the deletion process.
        """
        try:
            if not await self.collection_exists():
                return False

            await self._client.drop_collection(self._physical_name)
            return True
        except Exception as e:
            raise Exception(f"Failed to delete collection {self._collection_name}: {str(e)}")

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
            batch_size: Rows per upsert round-trip.
            sparse_embeddings: Optional {token_id: weight} mappings, aligned with
                documents. The collection must have been created with
                with_sparse (see supports_sparse).

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

        rows = self._to_rows(documents = documents,
                             embeddings = embeddings,
                             sparse_embeddings = sparse_embeddings)

        # Batch upserts to bound request size instead of one giant call
        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start:batch_start + batch_size]
            await self._client.upsert(collection_name = self._physical_name,
                                      data = batch)
        return len(rows)

    @staticmethod
    def _to_rows(documents: Sequence[Document],
                 embeddings: Sequence[Embedding],
                 sparse_embeddings: Optional[Sequence[SparseEmbedding]]) -> List[Dict[str, Any]]:
        """
        Build the rows Milvus stores, one per document.

        Args:
            documents: Documents to store.
            embeddings: Dense vectors, aligned with documents.
            sparse_embeddings: Optional sparse vectors, aligned with documents.

        Returns:
            List[Dict[str, Any]]: Rows ready for upsert.
        """
        rows: List[Dict[str, Any]] = []
        for index, document in enumerate(documents):
            row: Dict[str, Any] = {ID_FIELD: str(uuid4()),
                                   CONTENT_FIELD: document.page_content,
                                   METADATA_FIELD: document.metadata or {},
                                   DENSE_VECTOR_NAME: embeddings[index]}
            if sparse_embeddings is not None:
                # Milvus rejects an empty sparse vector, and a chunk the sparse
                # model gave no weight to would produce one - the row is still
                # worth storing for dense retrieval, so it gets a single
                # zero-weight token instead of failing the whole batch
                row[SPARSE_VECTOR_NAME] = sparse_embeddings[index] or {0: 0.0}
            rows.append(row)
        return rows

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

        With sparse vectors supplied this becomes a hybrid search: Milvus runs
        the dense and the sparse branch and fuses them with RRF server-side, so
        it stays one round-trip and the two incomparable score scales are never
        mixed - only their ranks are.

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
        if not query_vectors:
            return []

        expression = to_milvus_expression(filters)

        # Milvus takes every query in one request, so unlike Qdrant there is
        # nothing to fan out here
        if sparse_query_vectors:
            responses = await self._search_hybrid(query_vectors = query_vectors,
                                                  sparse_query_vectors = sparse_query_vectors,
                                                  limit = limit,
                                                  expression = expression,
                                                  score_threshold = score_threshold)
        else:
            responses = await self._search_dense(query_vectors = query_vectors,
                                                 limit = limit,
                                                 expression = expression,
                                                 score_threshold = score_threshold)

        return [self._to_retrieved_chunks(response) for response in responses]

    async def _search_dense(self,
                            query_vectors: Sequence[Embedding],
                            limit: int,
                            expression: Optional[str],
                            score_threshold: Optional[float]) -> List[List[dict]]:
        """
        Run a dense-only search for every query vector.

        Args:
            query_vectors: Dense query vectors.
            limit: Maximum hits per query.
            expression: Translated metadata filter, or None.
            score_threshold: Minimum dense score, or None.

        Returns:
            List[List[dict]]: Raw Milvus hits per query.
        """
        return await self._searching(lambda: self._client.search(
            collection_name = self._physical_name,
            data = list(query_vectors),
            anns_field = DENSE_VECTOR_NAME,
            limit = limit,
            filter = expression or "",
            output_fields = [CONTENT_FIELD, METADATA_FIELD],
            search_params = self._dense_search_params(score_threshold)))

    async def _search_hybrid(self,
                             query_vectors: Sequence[Embedding],
                             sparse_query_vectors: Sequence[SparseEmbedding],
                             limit: int,
                             expression: Optional[str],
                             score_threshold: Optional[float]) -> List[List[dict]]:
        """
        Run dense and sparse branches and let Milvus fuse them by rank.

        Each branch asks for more than `limit` candidates
        (hybrid_prefetch_multiplier): fusion can only reorder what it is given,
        so a document ranked just outside the dense top-k but first on the
        sparse side has to be in the pool to be able to win.

        score_threshold rides on the dense branch rather than the fused result,
        because it is expressed in similarity scale - applied to RRF output it
        would compare against ~1/(60+rank) values and drop everything.

        Args:
            query_vectors: Dense query vectors.
            sparse_query_vectors: Sparse query vectors, aligned with the dense ones.
            limit: Maximum hits per query.
            expression: Translated metadata filter, or None.
            score_threshold: Minimum dense score, or None.

        Returns:
            List[List[dict]]: Raw Milvus hits per query, already fused.
        """
        prefetch_limit = limit * self._hybrid_prefetch_multiplier
        # The filter belongs on both branches: it narrows what each retrieves,
        # so fusion never has to reject candidates it should not have seen
        branch_filter = {"expr": expression} if expression else {}
        requests = [
            AnnSearchRequest(data = list(query_vectors),
                             anns_field = DENSE_VECTOR_NAME,
                             param = self._dense_search_params(score_threshold),
                             limit = prefetch_limit,
                             **branch_filter),
            AnnSearchRequest(data = list(sparse_query_vectors),
                             anns_field = SPARSE_VECTOR_NAME,
                             param = {"metric_type": _SPARSE_METRIC},
                             limit = prefetch_limit,
                             **branch_filter),
        ]
        ranker = RRFRanker(k = self._rrf_k if self._rrf_k is not None else _DEFAULT_RRF_K)

        return await self._searching(lambda: self._client.hybrid_search(
            collection_name = self._physical_name,
            reqs = requests,
            ranker = ranker,
            limit = limit,
            output_fields = [CONTENT_FIELD, METADATA_FIELD]))

    @staticmethod
    def _dense_search_params(score_threshold: Optional[float]) -> Dict[str, Any]:
        """
        Search parameters for the dense field.

        Milvus expresses a score floor as a `radius`, which for COSINE is a
        lower bound on similarity - the same thing Qdrant calls score_threshold.

        Args:
            score_threshold: Minimum dense score, or None.

        Returns:
            Dict[str, Any]: Parameters for a dense search request.
        """
        params: Dict[str, Any] = {"metric_type": _DENSE_METRIC}
        if score_threshold is not None:
            params["params"] = {"radius": score_threshold}
        return params

    async def _searching(self,
                         search: Callable[[], Coroutine[Any, Any, List[List[dict]]]]) -> List[List[dict]]:
        """
        Run a search, loading the collection first if Milvus says it is not.

        Milvus only serves loaded collections and a restarted server comes back
        with everything unloaded, which Qdrant has no equivalent of. Loading
        lazily on the error keeps the normal path at one round-trip instead of
        checking the load state before every search.

        Args:
            search: Builds the search coroutine; called again after a load.

        Returns:
            List[List[dict]]: Raw Milvus hits per query.

        Raises:
            MilvusException: If the search failed for any other reason.
        """
        try:
            return await search()
        except MilvusException as e:
            if "not loaded" not in str(e):
                raise
            SystemLogger.info(f"Loading Milvus collection {self._physical_name} before search")
            await self._client.load_collection(self._physical_name)
            return await search()

    @staticmethod
    def _to_retrieved_chunks(hits: List[dict]) -> List[RetrievedChunk]:
        """Convert one query's Milvus hits into backend-neutral chunks."""
        chunks: List[RetrievedChunk] = []
        for hit in hits:
            entity = hit.get("entity") or {}
            chunks.append(RetrievedChunk(id = str(hit.get("id")),
                                         score = hit.get("distance"),
                                         content = entity.get(CONTENT_FIELD, ""),
                                         metadata = entity.get(METADATA_FIELD) or {}))
        return chunks
