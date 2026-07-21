import os
from app.utils.config_loader import get_yaml_config

# Load model configuration from YAML
yaml_config = get_yaml_config()
models_config = yaml_config.get_section("models")

# Model config
DENSE_MODEL_NAME = os.getenv("DENSE_MODEL_NAME", models_config.get("dense_model_name", "Qwen/Qwen3-Embedding-0.6B"))
VLLM_DENSE_EMBEDDING_URL = os.getenv("VLLM_DENSE_EMBEDDING_URL", "http://172.17.0.1:8100/v1")

