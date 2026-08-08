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

class UnsupportedSearchTypeException(AppBaseException):
    """Raised when a caller pins a search type this vector store cannot answer.

    A 400 rather than a silent fallback to dense: a caller who named hybrid
    explicitly and got dense results back would be measuring retrieval quality
    on a configuration they do not think they are running, which is far harder
    to notice than a rejected request.
    """
    def __init__(self,
                 search_type: str,
                 reason: str,
                 type: str = "invalid_request_error",
                 params: Any = "search_type",
                 code: Any = "unsupported_value") -> None:
        super().__init__(status_code = status.HTTP_400_BAD_REQUEST,
                         response = BaseResponse(message = f"Search type '{search_type}' is not available for this vector store: {reason}.",
                                                 type = type,
                                                 params = params,
                                                 code = code))

class UnsupportedMultipleFilesException(AppBaseException):
    def __init__(self,
                 num_files: int,
                 type: str = "invalid_request_error",
                 params: Any = "file_ids",
                 code: Any = "unsupported_value") -> None:
        super().__init__(status_code = status.HTTP_400_BAD_REQUEST,
                         response = BaseResponse(message = f"Only a single file is currently supported per vector store, got {num_files}.",
                                                 type = type,
                                                 params = params,
                                                 code = code))