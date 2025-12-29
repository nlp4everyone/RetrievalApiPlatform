CREATE_FILE_TABLE = """
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    bytes BIGINT NOT NULL,
    purpose TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    content_type TEXT,
    metadata JSONB
);
"""

CREATE_VECTOR_STORE_TABLE = """
CREATE TABLE IF NOT EXISTS vector_stores (
    id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    name TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    last_active_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    usage_bytes BIGINT NOT NULL,
    metadata JSONB,
    expires_at TIMESTAMPTZ,
    expires_after BIGINT,
    chunking_strategy JSONB,
    vector_store_type TEXT
);
"""


CREATE_FILE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_files_api_key_purpose_created_at_id
ON files (api_key, purpose, created_at, id);
"""

CREATE_VECTOR_STORE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_vector_stores_api_key_created_at_id
ON vector_stores (api_key, created_at, id);
"""