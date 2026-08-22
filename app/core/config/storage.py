from .settings import settings
from app.utils.config_loader import get_yaml_config

# Load interaction settings from YAML
yaml_config = get_yaml_config()
storage_config = yaml_config.get_section("storage")

# Minio configuration. MINIO_API_PORT / MINIO_CONSOLE_PORT are not re-exported:
# only docker-compose consumes them, straight from .env.
MINIO_ROOT_USER = settings.MINIO_ROOT_USER
MINIO_ROOT_PASSWORD = settings.MINIO_ROOT_PASSWORD
MINIO_ENDPOINT_URL = settings.MINIO_ENDPOINT_URL

# Uploaded File config
UPLOADED_FILE_BUCKET = storage_config.get("uploaded_file_bucket")
MAX_FILE_SIZE = storage_config.get("max_file_size")

# Parsed Markdown produced by ParseStage. Its own bucket, not a prefix inside
# UPLOADED_FILE_BUCKET: derived data and original uploads want different
# retention, and a bucket is what a lifecycle rule applies to.
PARSED_TEXT_BUCKET = storage_config.get("parsed_text_bucket", "parsed-texts")

# Thread pool for blocking MinIO I/O (upload/download/delete), kept separate
# from CPU-bound work (chunking) so a slow download can't starve it
IO_THREAD_POOL_SIZE = storage_config.get("io_thread_pool_size", 32)

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
