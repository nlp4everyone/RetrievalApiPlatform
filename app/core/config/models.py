import os
from app.utils.config_loader import get_toml_config

# Load interaction settings from TOML
toml_config = get_toml_config()
docker_config = toml_config.get_section("docker")
api_config = toml_config.get_section("api")

# Model config
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME")
DOCKER_EMBEDDING_PORT = docker_config.get("DOCKER_EMBEDDING_PORT")
EMBEDDING_SERVICE_NAME = docker_config.get("EMBEDDING_SERVICE_NAME")

