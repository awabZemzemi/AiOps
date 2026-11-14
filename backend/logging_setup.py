"""Local investigation telemetry; no dependency on agent reasoning or prompts."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

REDACTED = "[REDACTED]"
SENSITIVE_KEY = re.compile(
    r"password|passwd|pwd|secret|token|api[_-]?key|authorization|credential|"
    r"cookie|private[_-]?key|connection[_-]?string|\bdsn\b|email|phone|"
    r"address|customer|session|user[_-]?(?:name|id)|stringData", re.I,
)
TOKEN_METRIC = re.compile(r"^(?:token_usage|token_count|(?:input|output|total|cached|reasoning)_tokens)$", re.I)
TEXT_PATTERNS = [
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S), REDACTED),
    (re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I), REDACTED),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@]+(?::[^\s/@]*)?@", re.I), r"\1[REDACTED]@"),
    (re.compile(r"\b(?:gsk_|sk-|AKIA|ASIA)[A-Za-z0-9_-]{12,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), REDACTED),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), REDACTED),
]
ASSIGNMENT = re.compile(
    r"(?P<key>[\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|authorization|"
    r"credential|cookie|email|phone|address|username|user_id)[\w.-]*)"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;&<>]+)", re.I,
)


def redact(value, secrets: tuple[str, ...] = ()):
    """Redact structured fields and common secrets/PII inside free-form evidence.

    Treat JSON embedded in message content as structured data, too. Unknown opaque
    objects are never persisted through repr(), which may contain credentials.
    """
    if isinstance(value, dict):
        # Kubernetes/env-style {name: SECRET_NAME, value: ...} records.
        secret_value = bool(SENSITIVE_KEY.search(str(value.get("name", ""))))
        return {
            key: REDACTED if (SENSITIVE_KEY.search(str(key)) and not TOKEN_METRIC.fullmatch(str(key)))
            or (key == "value" and secret_value)
            or (key == "data" and value.get("kind") == "Secret")
            else redact(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(redact(parsed, secrets), ensure_ascii=False)
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        for pattern, replacement in TEXT_PATTERNS:
            value = pattern.sub(replacement, value)
        value = ASSIGNMENT.sub(lambda m: m["key"] + m["sep"] + REDACTED, value)
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[UNSERIALIZABLE]"


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]):
        super().__init__()
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        # Never append logging's default raw exception/traceback representation.
        return json.dumps(redact(entry, self.secrets), ensure_ascii=True, allow_nan=False)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Keep new/rotated files private and never echo failed log payloads to stderr."""

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)

    def emit(self, record):
        try:
            line = self.format(record)
            lines = [line]
            if len(line) + 1 > self.maxBytes:
                # Keep storage bounded even for very large histories, without
                # truncating their contents. Reassemble fragments in part order.
                size = self.maxBytes // 8
                parts = [line[i:i + size] for i in range(0, len(line), size)]
                lines = [json.dumps({
                    "event": "investigation_log_fragment",
                    "investigation_id": getattr(record, "fields", {}).get("investigation_id"),
                    "part": index, "parts": len(parts), "json_fragment": part,
                }, ensure_ascii=True) for index, part in enumerate(parts)]
            for line in lines:
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
                      backup_count: int = 3) -> tuple[logging.Logger, logging.Logger]:
    """Configure two private rotating JSONL sinks once during application startup."""
    if max_bytes < 1024 or backup_count < 1:
        raise ValueError("Logging requires max_bytes >= 1024 and backup_count >= 1.")
    directory = directory or Path(os.environ.get(
        "AIOPS_LOG_DIR", Path(__file__).resolve().parents[1] / "logs" / "backend",
    ))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    secrets = tuple(sorted({
        value for key, value in os.environ.items()
        if SENSITIVE_KEY.search(key) and len(value) >= 4
    } | {
        value for value in os.environ.get("AIOPS_LOG_REDACT_VALUES", "").splitlines() if value
    }, key=len, reverse=True))
    loggers = []
    for name in ("operations", "messages"):
        logger = logging.getLogger(f"aiops.investigations.{name}")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = PrivateRotatingFileHandler(
            directory / f"{name}.jsonl", maxBytes=max_bytes,
            backupCount=backup_count, encoding="utf-8",
        )
        handler.setFormatter(JsonFormatter(secrets))
        logger.addHandler(handler)
        loggers.append(logger)
    return tuple(loggers)


def log_event(logger: logging.Logger, event: str, *, error: bool = False, **fields) -> None:
    logger.log(logging.ERROR if error else logging.INFO, event, extra={"fields": fields})
