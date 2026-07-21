from dotenv import load_dotenv

# Load environment variables once at module import
load_dotenv()

# Import all configuration modules
from .database import *
from .mlflow import *
from .models import *
from .storage import *
from .api import *
from .redis import *
from .settings import settings