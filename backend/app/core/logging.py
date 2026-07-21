import logging
import sys
from typing import cast

import structlog


def configure_logging(log_level: str) -> None:
    """Configure application logs as newline-delimited JSON."""
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        stream=sys.stdout,
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structured logger bound to a component name."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(component=name))
