from .settings import settings
from app.utils.config_loader import get_yaml_config

# Load Redis configuration from YAML
yaml_config = get_yaml_config()
redis_config = yaml_config.get_section("redis")

# Redis configuration from YAML
REDIS_URL = redis_config.get("url")
REDIS_PORT = settings.REDIS_PORT