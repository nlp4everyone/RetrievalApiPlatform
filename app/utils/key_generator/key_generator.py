import secrets, uuid, random
from typing import Literal


def generate_request_id() -> str:
    """
    Generate a unique request identifier.
    
    Creates a request ID in the format 'req_{uuid}' where the UUID is a 32-character
    hexadecimal string. This is useful for tracking and logging API requests.
    
    Returns:
        str: A unique request ID (e.g., 'req_a1b2c3d4e5f6789012345678901234ab')
    """
    return f"req_{uuid.uuid4().hex}"


def generate_fingerprint() -> str:
    """
    Generate a cryptographically secure fingerprint.
    
    Creates a fingerprint using secrets.token_hex() which provides a cryptographically
    secure random string. The fingerprint is prefixed with 'fp_' and contains 10 hex characters.
    
    Returns:
        str: A secure fingerprint (e.g., 'fp_a1b2c3d4e5')
    """
    return "fp_" + secrets.token_hex(5)


def generate_seed() -> int:
    """
    Generate a random seed for reproducible randomization.
    
    Creates a 64-bit random integer suitable for seeding random number generators.
    This is useful when you need reproducible randomness in testing or simulations.
    
    Returns:
        int: A 64-bit random seed value
    """
    return random.getrandbits(64)


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
    
    Creates a vector store ID in the format 'vs-{uuid}' where the UUID is a full
    32-character hexadecimal string. This is used for identifying vector stores
    in the system.
    
    Returns:
        str: A vector store ID (e.g., 'vs-a1b2c3d4e5f6789012345678901234ab')
    """
    return f"vs-{uuid.uuid4().hex}"