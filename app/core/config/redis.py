from app.utils.config_loader import get_toml_config

# Load Redis configuration from TOML
toml_config = get_toml_config()
redis_config = toml_config.get_section("redis")

# Redis configuration from TOML
REDIS_URL = redis_config.get("REDIS_URL")