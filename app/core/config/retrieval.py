from app.utils.config_loader import get_yaml_config

# Load retrieval settings from YAML
yaml_config = get_yaml_config()
retrieval_config = yaml_config.get_section("retrieval")

# Candidates each hybrid branch fetches, as a multiple of the requested limit.
# Fusion can only reorder the pool it is handed, so a document ranked just
# outside both top-k lists needs the extra depth to compete at all.
HYBRID_PREFETCH_MULTIPLIER = retrieval_config.get("hybrid_prefetch_multiplier", 2)

if not isinstance(HYBRID_PREFETCH_MULTIPLIER, int) or HYBRID_PREFETCH_MULTIPLIER < 1:
    raise ValueError("retrieval.hybrid_prefetch_multiplier must be an integer >= 1, "
                     f"got: {HYBRID_PREFETCH_MULTIPLIER!r}")

# RRF k: larger rewards cross-branch consensus, smaller rewards one strong hit.
# None keeps the backend default (60) and the request shape unchanged.
RRF_K = retrieval_config.get("rrf_k", None)

if RRF_K is not None and (not isinstance(RRF_K, int) or RRF_K < 1):
    raise ValueError(f"retrieval.rrf_k must be an integer >= 1 or empty, got: {RRF_K!r}")
