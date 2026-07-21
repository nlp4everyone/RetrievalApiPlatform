from fastapi import Request
from fastapi.responses import JSONResponse
from .base_exception import AppBaseException
from loggers import SystemLogger

async def common_exception_handler(request: Request, exc: AppBaseException):
    log_line = f"[{request.method}] {request.url.path} -> {exc.status_code}: {exc.log_message}"
    # 5xx is a server-side failure worth an error; 4xx is expected client
    # misuse (bad id, wrong key) and only needs a warning trail.
    if exc.status_code >= 500:
        SystemLogger.error(log_line)
    else:
        SystemLogger.warning(log_line)

    return JSONResponse(
        status_code = exc.status_code,
        content = exc.response.model_dump())