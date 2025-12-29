from ..base_exception import BaseException, BaseResponse
from typing import Any
from fastapi import status

class VectorStoreNotFoundException(BaseException):
    def __init__(self,
                 vector_store_id :str,
                 type: str = "invalid_request_error",
                 params: Any = "vector_store_id",
                 code: Any = None):
        super().__init__(status_code = status.HTTP_404_NOT_FOUND,
                         response = BaseResponse(message = f"No vector store found with id '{vector_store_id}'",
                                                 type = type,
                                                 params = params,
                                                 code = code))