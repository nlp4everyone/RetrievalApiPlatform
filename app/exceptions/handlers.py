from fastapi import Request
from fastapi.responses import JSONResponse
from .base_exception import AppBaseException

async def common_exception_handler(request: Request, exc: AppBaseException):
    return JSONResponse(
        status_code = exc.status_code,
        content = exc.response.model_dump())