import logging
import structlog
import sys
from logging.handlers import TimedRotatingFileHandler

# Base loggers and handlers
console_logger = logging.getLogger("console_logger")
console_logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_logger.addHandler(console_handler)
console_logger.propagate = False

file_logger = logging.getLogger("file_logger")
file_logger.setLevel(logging.INFO)
file_handler = TimedRotatingFileHandler("logs/sentinel_logs", when="midnight", backupCount=30)
file_handler.setLevel(logging.INFO)
file_logger.addHandler(file_handler)
file_logger.propagate = False

# Structlog wrappers with different renderers per destination
console_logger = structlog.wrap_logger(
    console_logger,
    processors=[
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
)

file_logger = structlog.wrap_logger(
    file_logger,
    processors=[
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(), 
    ],
)
