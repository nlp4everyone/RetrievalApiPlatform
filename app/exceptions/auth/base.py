from ..base_exception import AppBaseException, BaseResponse
from typing import Any
from fastapi import status

class APIKeyIncorrectException(AppBaseException):
    def __init__(self,
                 api_key: str,
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = None) -> None:
        # Mask the offending key before it reaches server logs; the raw value
        # still goes in the client-facing response, mirroring OpenAI's API.
        masked_key = f"{api_key[:4]}***" if len(api_key) > 4 else "***"
        super().__init__(status_code = status.HTTP_401_UNAUTHORIZED,
                         response = BaseResponse(message = f"Incorrect API key provided: '{api_key}'",
                                                 type = type,
                                                 params = params,
                                                 code = code),
                         log_message = f"Incorrect API key provided: '{masked_key}'")

class BearerMissingException(AppBaseException):
    def __init__(self,
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = None) -> None:
        super().__init__(status_code = status.HTTP_401_UNAUTHORIZED,
                         response = BaseResponse(message = "Missing bearer or basic authentication in header",
                                                 type = type,
                                                 params = params,
                                                 code = code))