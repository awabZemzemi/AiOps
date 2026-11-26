"""Local investigation telemetry; no dependency on agent reasoning or prompts."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

OPERATION_FIELDS = {"investigation_id", "status", "duration_ms", "tools_used", "tool_call_count", "error_type"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **{key: value for key, value in getattr(record, "fields", {}).items() if key in OPERATION_FIELDS},
        }
        # Never append logging's default raw exception/traceback representation.
        return json.dumps(entry, ensure_ascii=True, allow_nan=False)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Keep new/rotated files private and never echo failed log payloads to stderr."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def emit(self, record):
        try:
            line = self.format(record)
            if len(line) + 1 > self.maxBytes:
                self.handleError(record)
                return
            if self.stream is None:
                self.stream = self._open()
            self.stream.seek(0, 2)
            if self.stream.tell() + len(line) + 1 > self.maxBytes:
                self.doRollover()
            self.stream.write(line + "\n")
            self.flush()
        except Exception:
            self.handleError(record)

    def handleError(self, record):
        # logging's default handleError prints the record, potentially exposing data.
        sys.stderr.write(json.dumps({
            "level": "ERROR", "event": "investigation_log_write_failed",
            "investigation_id": getattr(record, "fields", {}).get("investigation_id"),
        }) + "\n")


def configure_logging(directory: Path | None = None, *, max_bytes: int = 5 * 1024 * 1024,
                      backup_count: int = 3) -> logging.Logger:
    """Configure the private rotating operations sink during application startup."""
    if max_bytes < 1024 or backup_count < 1:
        raise ValueError("Logging requires max_bytes >= 1024 and backup_count >= 1.")
    directory = directory or Path(os.environ.get(
        "AIOPS_LOG_DIR", Path(__file__).resolve().parents[2] / "logs" / "backend",
    ))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    logger = logging.getLogger("aiops.investigations.operations")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = PrivateRotatingFileHandler(
        directory / "operations.jsonl", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
    )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, *, error: bool = False, **fields) -> None:
    logger.log(logging.ERROR if error else logging.INFO, event, extra={"fields": fields})
