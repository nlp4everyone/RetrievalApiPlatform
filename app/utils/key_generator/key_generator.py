import uuid


# ---- Helper to generate OpenAI-style ID ----

def generate_file_id() -> str:
    """
    Generate an OpenAI-style file identifier.
    
    Creates a file ID in the format 'file-{8_char_uuid}' matching OpenAI's
    file ID format. This is useful for API compatibility with OpenAI clients.
    
    Returns:
        str: A file ID (e.g., 'file-a1b2c3d4')
    """
    return f"file-{uuid.uuid4().hex[:8]}"


def generate_vectorstore_id() -> str:
    """
    Generate a vector store identifier.
    
    Creates a vector store ID in the format 'vs_{uuid}' where the UUID is a full
    32-character hexadecimal string. This is used for identifying vector stores
    in the system.

    The separator is an underscore rather than the hyphen files use, which is
    OpenAI's own convention and, not by coincidence, the one character both
    backends accept: Qdrant takes either, Milvus allows only letters, digits and
    underscores. An id is therefore usable as a collection name unchanged on
    both engines, so the same store reads the same in Attu and in Qdrant's
    dashboard. Ids issued before this (``vs-…``) stay valid - see
    ``_collection_name`` in the Milvus backend, which still folds them.

    Returns:
        str: A vector store ID (e.g., 'vs_a1b2c3d4e5f6789012345678901234ab')
    """
    return f"vs_{uuid.uuid4().hex}"