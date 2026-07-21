import sys
from loguru import logger
from app.core.config import settings

# Remove existing handlers
logger.remove()
# Custom log
general_format = "<level>{level}</level> | {level.icon} | " \
                 "{time:YYYY-MM-DD HH:mm:ss} | " \
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

class SystemLogger:
    @staticmethod
    def info(message :str,
             *args,
             **kwargs):
        logger.opt(depth = depth).info(message,
                                       *args,
                                       **kwargs)

    @staticmethod
    def success(message: str,
                *args,
                **kwargs):
        logger.opt(depth = depth).success(message,
                                          *args,
                                          **kwargs)

    @staticmethod
    def warning(message: str,
                *args,
                **kwargs):
        logger.opt(depth = depth).warning(message,
                                          *args,
                                          **kwargs)

    @staticmethod
    def error(message: str,
              *args,
              **kwargs):
        logger.opt(depth = depth).error(message,
                                        *args,
                                        **kwargs)