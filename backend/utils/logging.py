"""
Structured logging utility with task_id / user_id / agent / timestamp context.

Uses structlog for structured JSON logging, suitable for observability
and auditability requirements (Constitution VI).
"""

import logging
import structlog


def configure_logging(log_level: str = "INFO") -> None:
    """
    Configure structured logging for the application.

    Outputs JSON-formatted logs to stdout. Each log entry includes
    timestamp, level, logger name, and any bound context variables
    (task_id, user_id, agent, etc.).

    Usage:
        from backend.utils.logging import configure_logging, get_logger
        configure_logging("INFO")

        logger = get_logger(__name__)
        logger = logger.bind(task_id="abc-123", user_id="user-1")
        logger.info("Research phase started", phase="planning")
    """

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set the standard library log level too
    logging.basicConfig(
        format="%(message)s",
        level=log_level.upper(),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance for the given name."""
    return structlog.get_logger(name)
