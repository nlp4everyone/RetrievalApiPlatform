import os
from app.utils.config_loader import get_yaml_config

# Load interaction settings from YAML
yaml_config = get_yaml_config()
storage_config = yaml_config.get_section("storage")
# Minio configuration
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD")

# Uploaded File config
UPLOADED_FILE_BUCKET = storage_config.get("uploaded_file_bucket")
MAX_FILE_SIZE = storage_config.get("max_file_size")

# API Versioning - load from YAML with ENV override
api_config = yaml_config.get_section("api")
API_VERSION = os.getenv("API_VERSION", api_config.get("version", "v1"))

# Allowed MIME types for file upload
ALLOWED_MIME_TYPES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/plain",
    "text/csv",
    "application/json",
    "image/jpeg",
    "image/png",
    "image/gif",
]

# Allowed file extensions
ALLOWED_EXTENSIONS = [
    ".pdf",
    ".docx",
    ".txt",
    ".csv",
    ".json",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
]

# MIME type to extension mapping
MIME_TYPE_MAPPING = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}
