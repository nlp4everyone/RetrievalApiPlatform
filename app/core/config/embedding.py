from app.utils.config_loader import get_yaml_config

# Load embedding configuration from YAML
yaml_config = get_yaml_config()
embedding_config = yaml_config.get_section("embedding")

# Chunks embedded/upserted per round-trip - bounds peak memory regardless of file size
EMBEDDING_UPLOAD_BATCH_SIZE = embedding_config.get("upload_batch_size")
# Batches allowed to run concurrently - peak memory stays ~SIZE * CONCURRENCY, not file-sized
EMBEDDING_BATCH_CONCURRENCY = embedding_config.get("batch_concurrency")
