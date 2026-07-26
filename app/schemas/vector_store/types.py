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

    Adding a keyword/BM25 retriever would add KEYWORD (BM25 alone) and HYBRID
    (dense + BM25 merged by a real fusion strategy).
    """
    DENSE = "dense"