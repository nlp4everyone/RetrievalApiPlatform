import sys, logging
from loguru import logger
from app.core.config import settings
from app.core.request_context import request_id_ctx

# Remove existing handlers
logger.remove()
# Default request_id for logs outside any request/task context
logger.configure(extra={"request_id": "-"})
general_format = "<level>{level}</level> | {level.icon} | " \
                 "{time:YYYY-MM-DD HH:mm:ss} | " \
                 "{extra[request_id]} | " \
                 "{file.name}:{line} - " \
                 "<level>{message}</level>"

# "auto" (default): console on a real TTY, JSON otherwise. Set LOG_FORMAT to override.
if settings.LOG_FORMAT == "auto":
    _use_json = not sys.stderr.isatty()
else:
    _use_json = settings.LOG_FORMAT == "json"

logger.add(sys.stderr,
          format = general_format,
          level = settings.LOG_LEVEL,
          colorize = not _use_json,
          serialize = _use_json)

logger.level("INFO", color="<blue>")
logger.level("SUCCESS", color="<green>")
logger.level("WARNING", color="<yellow>")
logger.level("ERROR", color="<red>")
depth = 1

class InterceptHandler(logging.Handler):
    """Redirects stdlib `logging` records (uvicorn's access/error logs) into loguru."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Walk past logging's own internal frames so loguru attributes the
        # line to the real caller (e.g. uvicorn) instead of logging/__init__.py.
        frame = sys._getframe(1)
        frame_depth = 1
        while frame is not None and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            frame_depth += 1

        logger.opt(depth = frame_depth, exception = record.exc_info).bind(
            request_id = request_id_ctx.get()
        ).log(level, record.getMessage())

# Route uvicorn's own loggers through loguru instead of their default handlers.
for _uvicorn_logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _uvicorn_logger = logging.getLogger(_uvicorn_logger_name)
    _uvicorn_logger.handlers = [InterceptHandler()]
    _uvicorn_logger.propagate = False
    # Set explicitly - don't rely on uvicorn's own dictConfig having run first.
    _uvicorn_logger.setLevel(settings.LOG_LEVEL)

def _bound(exception: bool = False):
    # Binds request_id for correlation; exception=True attaches the current
    # traceback (call from inside an `except` block).
    return logger.opt(depth = depth, exception = exception).bind(request_id = request_id_ctx.get())

class SystemLogger:
    @staticmethod
    def debug(message :str,
              *args,
              **kwargs):
        _bound().debug(message,
                       *args,
                       **kwargs)

    @staticmethod
    def info(message :str,
             *args,
             **kwargs):
        _bound().info(message,
                      *args,
                      **kwargs)

    @staticmethod
    def success(message: str,
                *args,
                **kwargs):
        _bound().success(message,
                         *args,
                         **kwargs)

    @staticmethod
    def warning(message: str,
                *args,
                **kwargs):
        _bound().warning(message,
                         *args,
                         **kwargs)

    @staticmethod
    def error(message: str,
              *args,
              exc_info: bool = False,
              **kwargs):
        _bound(exception = exc_info).error(message,
                                           *args,
                                           **kwargs)