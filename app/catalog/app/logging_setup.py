import json
import logging
import time
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": self.service,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(app, service: str) -> logging.Logger:
    logger = logging.getLogger(f"sentinelshop.{service}")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    logger.handlers = [handler]
    logger.propagate = False

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error")
            response = JSONResponse(
                status_code=500, content={"detail": "Internal server error"}
            )
        logger.info("Request completed", extra={"fields": {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }})
        return response

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return logger
