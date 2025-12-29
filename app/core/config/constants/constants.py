import os
from dotenv import load_dotenv

# Load the .env file
load_dotenv()

# Model config
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME")
# Service
MLFLOW_TRACKING_URI = "http://mlflow:5000"
MLFLOW_S3_ENDPOINT_URL = "http://minio:9000"

# Uploaded File config
UPLOADED_FILE_BUCKET = "uploaded-files"
MAX_FILE_SIZE = 100 # In Megabytes
