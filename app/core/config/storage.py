import os
from app.utils.config_loader import get_toml_config

# Load interaction settings from TOML
toml_config = get_toml_config()
storage_config = toml_config.get_section("storage")
# Minio configuration
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD")

# Uploaded File config
UPLOADED_FILE_BUCKET = storage_config.get("UPLOADED_FILE_BUCKET")
MAX_FILE_SIZE = storage_config.get("MAX_FILE_SIZE")
