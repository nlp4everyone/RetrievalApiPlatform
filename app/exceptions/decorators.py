import asyncpg, functools, inspect, socket
from fastapi import HTTPException, status

from .base_exception import AppBaseException
from .postgres import PostgresConnectionException
from loggers import SystemLogger


def postgres_errors(action: str):
    """
    Wrap an async service method with the standard Postgres/HTTP error handling:
    asyncpg/socket errors become PostgresConnectionException, AppBaseException and
    HTTPException pass through unchanged, anything else becomes a 500.

    `action` is a present-participle phrase describing the operation for logs and
    the HTTPException detail, e.g. "creating vector store" or "getting file
    {file_id}" - placeholders are filled from the decorated function's bound
    arguments, so they must be parameter names of that function.
    """
    def decorator(func):
        signature = inspect.signature(func)

        def describe(args, kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            return action.format(**bound)

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except (asyncpg.PostgresError, socket.gaierror) as e:
                SystemLogger.error(f"Postgres connection failed while {describe(args, kwargs)}: {e}", exc_info=True)
                raise PostgresConnectionException()
            except (AppBaseException, HTTPException):
                raise
            except Exception as e:
                SystemLogger.error(f"Error {describe(args, kwargs)}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error {describe(args, kwargs)}: {str(e)}"
                )
        return wrapper
    return decorator
