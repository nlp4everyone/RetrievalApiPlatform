from .settings import settings
from app.utils.config_loader import get_yaml_config

# Load model configuration from YAML
yaml_config = get_yaml_config()
models_config = yaml_config.get_section("models")

# Model config
DENSE_MODEL_NAME = settings.DENSE_MODEL_NAME

