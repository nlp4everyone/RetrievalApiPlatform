from ..base_exception import AppBaseException, BaseResponse
from typing import Any, Union
from fastapi import status

class FileSizeLimitExceededException(AppBaseException):
    def __init__(self,
                 max_size :Union[int,float],
                 current_size :Union[int,float],
                 type: str = "invalid_request_error",
                 params: Any = None,
                 code: Any = None) -> None:
        super().__init__(status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                         response = BaseResponse(message = f"File too large. Maximum allowed size is {max_size} MB, got {current_size:.2f} MB.",
                                                 type = type,
                                                 params = params,
                                                 code = code))

class FileNotFoundException(AppBaseException):
    def __init__(self,
                 file_id :str,
                 type: str = "invalid_request_error",
                 params: Any = "id",
                 code: Any = None) -> None:
        super().__init__(status_code = status.HTTP_404_NOT_FOUND,
                         response = BaseResponse(message = f"No such File object: {file_id}",
                                                 type = type,
                                                 params = params,
                                                 code = code))