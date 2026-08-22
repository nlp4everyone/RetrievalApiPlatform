from app.utils.config_loader import get_yaml_config

# Load ingestion pipeline settings from YAML
yaml_config = get_yaml_config()
ingestion_config = yaml_config.get_section("ingestion")

# Thread pool for CPU-bound chunking work, sized to the machine's cores -
# separate from IO_THREAD_POOL_SIZE so chunking never queues behind a slow download
CPU_THREAD_POOL_SIZE = ingestion_config.get("cpu_thread_pool_size", 4)

# Max MinIO operations a worker process runs at once - downloads, parse-cache
# lookups and artifact uploads all draw on the same IO_THREAD_POOL_SIZE, so one
# limit covers the lot. Two separate limits would have to be added up by hand
# every time a new consumer of that pool appears, which is the check that gets
# forgotten; keep this comfortably below IO_THREAD_POOL_SIZE.
STORAGE_CONCURRENCY = ingestion_config.get("storage_concurrency", 8)