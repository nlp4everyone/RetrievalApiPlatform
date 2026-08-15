from .settings import settings

# Postgres configuration. POSTGRES_PORT is not re-exported: app.startup connects
# on the container's own 5432, while .env's value is the host port compose maps.
POSTGRES_USER = settings.POSTGRES_USER
POSTGRES_PASSWORD = settings.POSTGRES_PASSWORD
POSTGRES_DB = settings.POSTGRES_DB
POSTGRES_HOST = settings.POSTGRES_HOST

# Backend new vector stores are created on ("qdrant" or "milvus")
VECTOR_STORE_PROVIDER = settings.VECTOR_STORE_PROVIDER
# Backends startup connects, default first: the default plus every backend whose
# settings are filled in. Derived, not an env var - see Settings.enabled_vector_store_providers
CONNECTED_VECTOR_STORE_PROVIDERS = settings.enabled_vector_store_providers

# Qdrant configuration
QDRANT_URL = settings.QDRANT_URL
QDRANT_API_KEY = settings.QDRANT_API_KEY

# Milvus configuration
MILVUS_URI = settings.MILVUS_URI
MILVUS_TOKEN = settings.MILVUS_TOKEN
