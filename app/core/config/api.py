import os
from app.utils.config_loader import get_toml_config
# Load interaction settings from TOML
toml_config = get_toml_config()
api_config = toml_config.get_section("api")

# Other service API keys
UNDATASIO_API_KEY = os.getenv("UNDATASIO_API_KEY")
LLAMAPARSE_API_KEY = os.getenv("LLAMAPARSE_API_KEY")

# API keys
SERVING_API_KEY = os.getenv("SERVING_API_KEY")
FASTAPI_API_KEY = api_config.get("FASTAPI_API_KEY")