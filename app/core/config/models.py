import os

# Model config
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
VLLM_DENSE_EMBEDDING_URL = os.getenv("VLLM_DENSE_EMBEDDING_URL", "http://172.17.0.1:8100/v1")

