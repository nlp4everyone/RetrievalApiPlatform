import sys
from loguru import logger
from app.core.config import settings
from app.core.request_context import request_id_ctx

# Remove existing handlers
logger.remove()
# Default extra for log lines emitted outside any request/task context
# (startup, worker init, etc.)
logger.configure(extra={"request_id": "-"})
# Custom log
general_format = "<level>{level}</level> | {level.icon} | " \
                 "{time:YYYY-MM-DD HH:mm:ss} | " \
                 "{extra[request_id]} | " \
                 "{file.name}:{line} - " \
                 "<level>{message}</level>"

# LOG_FORMAT="auto" (default) picks console for a real terminal and JSON otherwise,
# so a plain TTY still gets colorized human-readable lines while docker logs / a log
# aggregator gets structured JSON with no ANSI codes. Set LOG_FORMAT explicitly to
# override the guess (e.g. JSON logs while running docker compose locally, or console
# logs in a piped CI shell).
if settings.LOG_FORMAT == "auto":
    _use_json = not sys.stderr.isatty()
else:
    _use_json = settings.LOG_FORMAT == "json"

# Add a single handler that captures all logs
logger.add(sys.stderr,
          format = general_format,
          level = settings.LOG_LEVEL,
          colorize = not _use_json,
          serialize = _use_json)

# Level color
logger.level("INFO", color="<blue>")   # Green background
logger.level("SUCCESS", color="<green>")
logger.level("WARNING", color="<yellow>") # Yellow background
logger.level("ERROR", color="<red>")   # Red background
depth = 1

def _bound():
    # Attach the current request/task's ID (set by RequestIDMiddleware or the
    # TaskIQ worker) to this log line, so it can be grepped across the whole
    # request -> service -> worker path.
    return logger.opt(depth = depth).bind(request_id = request_id_ctx.get())

class SystemLogger:
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
              **kwargs):
        _bound().error(message,
                       *args,
                       **kwargs)