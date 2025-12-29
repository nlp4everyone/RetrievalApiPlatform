import secrets, uuid, random
from typing import Literal

def generate_request_id():
    return f"req_{uuid.uuid4().hex}"

def generate_fingerprint():
    return "fp_" + secrets.token_hex(5)

def generate_seed():
    return random.getrandbits(64)

# ---- Helper to generate OpenAI-style ID ----
def generate_file_id() -> str:
    return f"file-{uuid.uuid4().hex[:8]}"

def generate_vectorstore_id() -> str:
    return f"vs-{uuid.uuid4().hex}"