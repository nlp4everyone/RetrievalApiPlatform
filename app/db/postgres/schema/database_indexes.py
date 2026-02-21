CREATE_FILE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_files_api_key_purpose_created_at_id
ON files (api_key, purpose, created_at, id);
"""

CREATE_VECTOR_STORE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_vector_stores_api_key_created_at_id
ON vector_stores (api_key, created_at, id);
"""