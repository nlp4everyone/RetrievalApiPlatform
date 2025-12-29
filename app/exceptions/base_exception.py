from typing import Any
from pydantic import BaseModel
from fastapi import status

class BaseResponse(BaseModel):
    message :str
    type :str
    params :Any = None
    code :Any = None

class BaseException(Exception):
    def __init__(self, status_code: int, response: BaseResponse):
        self.status_code = status_code
        self.response = response

class WrongPrefixException(BaseException):
    def __init__(self,
                 input :str,
                 type: str = "invalid_request_error",
                 params: str = None,
                 prefix :str = None,
                 code: Any = "invalid_value"):
        # Split input type
        prefix = params.split("_")[0] if prefix is None else prefix
        # Inherit
        super().__init__(status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                         response = BaseResponse(message = f"Invalid '{params}': '{input}'. Expected an ID that begins with '{prefix}'.",
                                                 type = type,
                                                 params = params,
                                                 code = code))