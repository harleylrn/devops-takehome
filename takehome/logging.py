import json
import logging
import sys
from datetime import UTC, datetime

LOG = logging.getLogger("takehome")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Never serialize exception strings, settings, payloads or arbitrary extra fields.
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key in ("job_id", "error_type", "attempt", "status", "service"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    LOG.handlers = [handler]
    LOG.setLevel(level)
    LOG.propagate = False
    # Connection libraries may include credential-bearing URLs in their diagnostics.
    for name in ("aio_pika", "aiormq", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False


def event(name: str, **fields) -> None:
    LOG.info(name, extra=fields)
