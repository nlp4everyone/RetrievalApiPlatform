from strenum import StrEnum

class VectorStoreType(StrEnum):
    QDRANT = "qdrant"
    MILVUS = "milvus"


class SearchType(StrEnum):
    """How a search query is answered.

    One value, not a retriever list plus a fusion strategy, because those two
    always have to agree - two retrievers with a passthrough fusion silently
    throws half the results away. Naming the whole shape keeps the invalid
    combinations unrepresentable.

    A caller may pin either value on a search request, but does not have to:
    left unset, the search type is resolved per search from what the target
    collection actually holds (see resolve_search_type), since a store ingested
    before sparse embedding was enabled has no sparse field to search. Pinning
    HYBRID on such a store is refused rather than quietly answered with dense
    results (see hybrid_unavailable_reason).
    """
    DENSE = "dense"
    HYBRID = "hybrid"