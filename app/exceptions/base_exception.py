from typing import Any, Optional
from pydantic import BaseModel
from fastapi import status

class BaseResponse(BaseModel):
    message :str
    type :str
    params :Any = None
    code :Any = None

class AppBaseException(Exception):
    def __init__(self,
                 status_code: int,
                 response: BaseResponse,
                 log_message: Optional[str] = None) -> None:
        self.status_code = status_code
        self.response = response
        # What gets written to server logs. Defaults to the client-facing message,
        # but subclasses whose response embeds sensitive data (e.g. an API key)
        # should pass a redacted log_message instead.
        self.log_message = log_message or response.message

class WrongPrefixException(AppBaseException):
    def __init__(self,
                 input :str,
                 type: str = "invalid_request_error",
                 params: Optional[str] = None,
                 prefix :Optional[str] = None,
                 code: Any = "invalid_value") -> None:
        # Split input type
        prefix = params.split("_")[0] if prefix is None else prefix
        # Inherit
        super().__init__(status_code = status.HTTP_400_BAD_REQUEST,
                         response = BaseResponse(message = f"Invalid '{params}': '{input}'. Expected an ID that begins with '{prefix}'.",
                                                 type = type,
                                                 params = params,
                                                 code = code))