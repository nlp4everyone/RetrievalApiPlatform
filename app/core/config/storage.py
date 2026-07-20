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
