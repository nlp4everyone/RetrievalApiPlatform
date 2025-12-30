import sys
from loguru import logger
# Remove existing handlers
logger.remove()
# Custom log
general_format = "<level>{level}</level> | {level.icon} | " \
                 "{time:YYYY-MM-DD HH:mm:ss} | " \
                 "{file.name}:{line} - " \
                 "<level>{message}</level>"
# Add custom log
# Add a single handler that captures all logs
logger.add(sys.stderr, format = general_format, level = "INFO", colorize = True)

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