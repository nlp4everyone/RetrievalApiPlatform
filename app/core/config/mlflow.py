from .settings import settings
from app.utils.config_loader import get_yaml_config

# Load MLflow configuration from YAML
yaml_config = get_yaml_config()
mlflow_config = yaml_config.get_section("mlflow")

# MLflow service
MLFLOW_TRACKING_URI = settings.MLFLOW_TRACKING_URI
MLFLOW_EXPERIMENT_NAME = settings.MLFLOW_EXPERIMENT_NAME
MLFLOW_DEFAULT_ARTIFACT_ROOT = settings.MLFLOW_DEFAULT_ARTIFACT_ROOT
MLFLOW_S3_ENDPOINT_URL = settings.MLFLOW_S3_ENDPOINT_URL
MLFLOW_PORT = settings.MLFLOW_PORT
