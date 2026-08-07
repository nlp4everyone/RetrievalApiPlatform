from .settings import settings
from app.utils.config_loader import get_yaml_config

# Load embedding configuration from YAML
yaml_config = get_yaml_config()
embedding_config = yaml_config.get_section("embedding")

# Chunks embedded/upserted per round-trip - bounds peak memory regardless of file size
EMBEDDING_UPLOAD_BATCH_SIZE = embedding_config.get("upload_batch_size")
# Batches allowed to run concurrently - peak memory stays ~SIZE * CONCURRENCY, not file-sized
EMBEDDING_BATCH_CONCURRENCY = embedding_config.get("batch_concurrency")

# Chunking provider selection ("chonkie" or "langchain")
CHUNKING_PROVIDER = settings.CHUNKING_PROVIDER

# Embedding provider selection ("openai" or "tei") and the connection config
# every provider shares
EMBEDDING_PROVIDER = settings.EMBEDDING_PROVIDER
DENSE_EMBEDDING_URL = settings.DENSE_EMBEDDING_URL
DENSE_EMBEDDING_API_KEY = settings.DENSE_EMBEDDING_API_KEY

# Sparse (lexical) embedding - opt-in, and only initialized at startup when
# SPARSE_EMBEDDING_ENABLED is true
SPARSE_EMBEDDING_ENABLED = settings.SPARSE_EMBEDDING_ENABLED
SPARSE_EMBEDDING_PROVIDER = settings.SPARSE_EMBEDDING_PROVIDER
SPARSE_EMBEDDING_URL = settings.SPARSE_EMBEDDING_URL
SPARSE_EMBEDDING_API_KEY = settings.SPARSE_EMBEDDING_API_KEY
