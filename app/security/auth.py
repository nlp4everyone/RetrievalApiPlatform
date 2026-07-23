# FastAPI Components
from fastapi import Depends
from fastapi.security import APIKeyHeader
# Typing
from typing import Optional
# Config
from app.core.config import FASTAPI_API_KEY
from app.exceptions.auth import *

api_key_header = APIKeyHeader(name = "Authorization", auto_error=False)

# Verify function
def verify_api_key(api_key: Optional[str] = Depends(api_key_header)) -> str:
    # Validate Authorization header
    if not api_key or not api_key.startswith("Bearer "):
        raise BearerMissingException()

    # Key
    token = api_key.split("Bearer ")[-1].strip()
    # Check token
    if token != FASTAPI_API_KEY:
        raise APIKeyIncorrectException(api_key = token,
                                       code = "invalid_api_key")
    return token