import os
from app.utils.config_loader import get_yaml_config

# Load MLflow configuration from YAML
yaml_config = get_yaml_config()
mlflow_config = yaml_config.get_section("mlflow")

# MLflow service
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI")
MLFLOW_EXPERIMENT_NAME = os.getenv("MLFLOW_EXPERIMENT_NAME", mlflow_config.get("experiment_name", "Experiment"))
MLFLOW_DEFAULT_ARTIFACT_ROOT = os.getenv("MLFLOW_DEFAULT_ARTIFACT_ROOT", mlflow_config.get("default_artifact_root", "s3://mlflow/artifacts"))
MLFLOW_S3_ENDPOINT_URL = os.getenv("MLFLOW_S3_ENDPOINT_URL")
