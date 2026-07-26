from .settings import settings

# Postgres configuration
POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD
POSTGRES_DB = settings.POSTGRES_DB
POSTGRES_HOST = settings.POSTGRES_HOST
POSTGRES_PORT = settings.POSTGRES_PORT

# Vector store provider selection ("qdrant" or "milvus")
VECTOR_STORE_PROVIDER = settings.VECTOR_STORE_PROVIDER

# Qdrant configuration
QDRANT_URL = settings.QDRANT_URL
QDRANT_API_KEY = settings.QDRANT_API_KEY
QDRANT_PORT = settings.QDRANT_PORT

# Milvus configuration (unused until the Milvus backend is implemented)
MILVUS_URI = settings.MILVUS_URI
MILVUS_TOKEN = settings.MILVUS_TOKEN
