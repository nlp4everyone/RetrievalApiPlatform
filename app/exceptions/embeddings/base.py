from ..base_exception import BaseException, BaseResponse
from typing import Any
from fastapi import status

class EmbeddingModelNotFoundException(BaseException):
    def __init__(self,
                 model_name: str,
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = "model_not_found"):
        super().__init__(status_code = status.HTTP_404_NOT_FOUND,
                         response = BaseResponse(message = f"The model `{model_name}` does not exist or you do not have access to it.",
                                                 type = type,
                                                 params = params,
                                                 code = code))