from ..base_exception import AppBaseException, BaseResponse
from typing import Any
from fastapi import status

class APIKeyIncorrectException(AppBaseException):
    def __init__(self,
                 api_key: str,
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = None):
        super().__init__(status_code = status.HTTP_401_UNAUTHORIZED,
                         response = BaseResponse(message = f"Incorrect API key provided: '{api_key}'",
                                                 type = type,
                                                 params = params,
                                                 code = code))

class BearerMissingException(AppBaseException):
    def __init__(self,
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = None):
        super().__init__(status_code = status.HTTP_401_UNAUTHORIZED,
                         response = BaseResponse(message = "Missing bearer or basic authentication in header",
                                                 type = type,
                                                 params = params,
                                                 code = code))