from app.utils.config_loader import get_yaml_config

# Load Redis configuration from YAML
yaml_config = get_yaml_config()
redis_config = yaml_config.get_section("redis")

# Redis configuration from YAML. REDIS_PORT is not re-exported: only
# docker-compose consumes it, straight from .env.
REDIS_URL = redis_config.get("url")