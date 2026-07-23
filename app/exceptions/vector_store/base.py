# Base exception
from ..base_exception import (AppBaseException,
                              BaseResponse,
                              WrongPrefixException)
# Typing
from typing import Any
# FastAPI
from fastapi import status

class VectorStoreNotFoundException(AppBaseException):
    def __init__(self,
                 vector_store_id :str,
                 type: str = "invalid_request_error",
                 params: Any = "vector_store_id",
                 code: Any = None) -> None:
        super().__init__(status_code = status.HTTP_404_NOT_FOUND,
                         response = BaseResponse(message = f"No vector store found with id '{vector_store_id}'",
                                                 type = type,
                                                 params = params,
                                                 code = code))

class WrongPrefixVectorstoreException(WrongPrefixException):
    def __init__(self,
                 input: str,
                 type: str = "invalid_request_error",
                 params: str = "vector_store_id",
                 prefix: str = "vs",
                 code: Any = "invalid_value") -> None:
        # Inherit
        super().__init__(input = input,
                         type = type,
                         prefix = prefix,
                         params = params,
                         code = code)