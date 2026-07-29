from app.utils.config_loader import get_yaml_config

# Load ingestion pipeline settings from YAML
yaml_config = get_yaml_config()
ingestion_config = yaml_config.get_section("ingestion")

# Thread pool for CPU-bound chunking work, sized to the machine's cores -
# separate from IO_THREAD_POOL_SIZE so chunking never queues behind a slow download
CPU_THREAD_POOL_SIZE = ingestion_config.get("cpu_thread_pool_size", 4)

# Max files a worker process downloads from MinIO at once, so many concurrent
# ingestion jobs can't exhaust IO_THREAD_POOL_SIZE on their own
DOWNLOAD_CONCURRENCY = ingestion_config.get("download_concurrency", 4)