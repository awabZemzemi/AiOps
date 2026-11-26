"""Minimize projected evidence and redact common sensitive text before release."""

import json
import os
import re
from typing import Literal

from diagnostic_service.evidence_common import COLLECTION_REASONS
from diagnostic_service.tools.container_logs import MAX_LOG_BYTES

from .schemas import MAX_CONTAINERS, ContainerLogs, Events, PodInformation, PodListing, PodReport, ResourceMetrics

REDACTED = "[REDACTED]"
MAX_OBSERVATION_BYTES = 64 * 1024
MAX_REPORT_BYTES = 256 * 1024
MAX_TEXT_BYTES = 16 * 1024

SCHEMAS = {
    "list_pods": PodListing,
    "get_pod_info": PodInformation,
    "get_logs": ContainerLogs,
    "get_events": Events,
    "get_metrics": ResourceMetrics,
    "pod_report": PodReport,
}
SENSITIVE_KEY = (
    r"password|passwd|pwd|secret|token|api[ _-]?key|authorization|credential|cookie|"
    r"private[ _-]?key|connection[ _-]?string|\bdsn\b|email|phone|user[ _-]?(?:name|id)|stringData"
)
SENSITIVE_FIELD = re.compile(SENSITIVE_KEY, re.I)
ASSIGNMENT = re.compile(
    rf"(?<![\w.-])(?P<key>[\w.-]{{0,64}}(?:{SENSITIVE_KEY})[\w.-]{{0,64}})"
    r"(?P<sep>[\"']?[ \t]*[:=][ \t]*)"
    r"(?:\"(?:\\.|[^\"\\\r\n])*(?:\"|\\?$)|'(?:\\.|[^'\\\r\n])*(?:'|\\?$)|[^\s,;&<>\"']+)", re.I,
)
SECRET_OBJECT = re.compile(r"\bkind[\"']?\s*:\s*[\"']?Secret\b", re.I)
PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z ]{0,40}PRIVATE KEY)-----[\s\S]*?(?:-----END \1-----|\Z)")
JSON_LOG_PREFIX = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.+Z-]+[ \t]+")
TEXT_PATTERNS = (
    (re.compile(r"\$argon2(?:id|i|d)\$[^\s\"'<>]+"), REDACTED),
    (PEM_BLOCK, REDACTED),
    (re.compile(r"\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9._~+/=-]+", re.I), REDACTED),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@]+@", re.I), r"\1[REDACTED]@"),
    (re.compile(r"\b(?:gsk_|sk-|AKIA|ASIA|gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}\b"), REDACTED),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), REDACTED),
    (re.compile(r"(?<![\w.+-])[\w.+-]{1,128}@[\w.-]{1,253}\.[A-Za-z]{2,63}\b"), REDACTED),
    (re.compile(r"(?<![\w.])(?:\+1[ -]?)?(?:\([2-9]\d{2}\)[ -]?|[2-9]\d{2}[ -])\d{3}[ -]\d{4}(?![\w.])"), REDACTED),
)
ERROR_MESSAGES = {
    "CollectionLimit": "Collection limit reached; evidence may be incomplete.",
    "PodIdentityChanged": "Metrics refer to a different Pod UID",
}
ERROR_TYPES = {"ApiException", "HTTPError", "OSError", *ERROR_MESSAGES}


def _redact_text(value: str, literals: tuple[str, ...]) -> str:
    if SECRET_OBJECT.search(value):
        return REDACTED
    if re.search(r"\b(?:Set-Cookie|Cookie)[ \t]*:", value, re.I):
        return REDACTED + ("\n" if value.endswith("\n") else "")
    for secret in literals:
        value = value.replace(secret, REDACTED)
    for pattern, replacement in TEXT_PATTERNS:
        value = pattern.sub(replacement, value)
    return ASSIGNMENT.sub(lambda match: match["key"] + match["sep"] + REDACTED, value)


def _redact_json(value, literals: tuple[str, ...], depth: int = 0):
    if depth > 16:
        raise ValueError("Structured text is too deeply nested.")
    if isinstance(value, dict):
        secret_object = value.get("kind") == "Secret"
        name = value.get("name")
        secret_env = isinstance(name, str) and SENSITIVE_FIELD.search(name)
        return {
            _redact_text(key, literals): REDACTED if (
                SENSITIVE_FIELD.search(key) or (secret_object and key == "data")
                or (secret_env and key == "value")
            ) else _redact_json(item, literals, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item, literals, depth + 1) for item in value]
    if isinstance(value, str):
        return _structured_text(value, literals, depth + 1) or _redact_text(value, literals)
    return value


def _unique_json_fields(pairs: list[tuple]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        # A later value can hide sensitive text in earlier duplicate fields.
        raise ValueError("Structured text has duplicate fields.")
    return result


def _structured_text(value: str, literals: tuple[str, ...], depth: int = 0) -> str | None:
    if not value.lstrip().startswith(("{", "[")):
        return None
    try:
        parsed = json.loads(value, object_pairs_hook=_unique_json_fields)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    safe = _redact_json(parsed, literals, depth)
    if safe == parsed:
        return value
    return json.dumps(safe, ensure_ascii=False) + value[len(value.rstrip("\r\n")):]


def sanitize_text(value: str) -> str:
    """Redact recognized secrets from bounded prose and structured log records."""
    if not isinstance(value, str) or len(value) > MAX_TEXT_BYTES or len(value.encode()) > MAX_TEXT_BYTES:
        raise ValueError("Text exceeds the processing limit.")
    literals = tuple(sorted({
        secret for key in ("AIOPS_JWT_SECRET", "GROQ_API_KEY")
        if len(secret := os.environ.get(key, "").strip()) >= 4
    } | set(filter(None, os.environ.get("AIOPS_LOG_REDACT_VALUES", "").splitlines())), key=len, reverse=True))
    # Strip whole PEM blocks before processing lines or JSON records.
    value = PEM_BLOCK.sub(REDACTED, value)
    structured = _structured_text(value, literals)
    if structured is not None:
        return structured
    if SECRET_OBJECT.search(value) and "\n" in value.strip():
        return REDACTED  # A multiline Secret can carry data outside the kind line.

    lines = []
    for line in value.splitlines(keepends=True):
        prefix = JSON_LOG_PREFIX.match(line)
        offset = prefix.end() if prefix else 0
        structured = _structured_text(line[offset:], literals)
        lines.append(line[:offset] + structured if structured is not None else _redact_text(line, literals))
    return "".join(lines)


def _sanitize_values(value):
    # Keys are Pydantic fields or independently checked container identifiers.
    if isinstance(value, dict):
        return {key: _sanitize_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_values(item) for item in value]
    return sanitize_text(value) if isinstance(value, str) else value


def safe_identifier(value: str) -> bool:
    """Keep the exact value of a typed identifier only if it needs no redaction."""
    return REDACTED not in value and sanitize_text(value) == value


def _minimize(data: dict, destination: str) -> None:
    pod = data.get("pod", {})
    if destination == "llm":
        for key in ("uid", "node_name", "pod_ip", "service_account_name"):
            pod.pop(key, None)
        for owner in pod.get("owner_references", []):
            owner.pop("uid", None)
            owner.pop("name", None)
        for volume in pod.get("volumes", []):
            volume.pop("name", None)
    for container in data.get("containers", []):
        if destination == "llm":
            container.pop("environment_variable_names", None)
        for probe in (container.get("probes") or {}).values():
            if not probe:
                continue
            http = probe.get("httpGet") or {}
            if http.get("path") is not None:
                http["path"] = http["path"].split("?", 1)[0]
            if destination == "llm":
                http.pop("httpHeaders", None)
                http.pop("host", None)
                (probe.get("tcpSocket") or {}).pop("host", None)
            for header in http.get("httpHeaders", []):
                header["value"] = REDACTED
            command = (probe.get("exec") or {}).get("command")
            if command is not None:
                probe["exec"]["command"] = [REDACTED] * len(command)


def _check_identities(data: dict, namespace: str, pod_name: str | None, container_name: str | None) -> None:
    identifiers = [namespace]
    if pod_name is not None:
        identifiers.append(pod_name)
    if data.get("namespace", namespace) != namespace or any(
        pod["namespace"] != namespace for pod in data.get("pods", [])
    ):
        raise ValueError("Unexpected namespace.")
    if "pod" in data and (data["pod"]["namespace"], data["pod"]["name"]) != (namespace, pod_name):
        raise ValueError("Unexpected Pod identity.")
    if "target" in data and data["target"] != {"namespace": namespace, "pod": pod_name}:
        raise ValueError("Unexpected report identity.")
    logs = data.get("logs", {})
    names = {container["name"] for container in data.get("containers", [])}
    if (container_name is not None and set(logs) != {container_name}) or ("target" in data and logs.keys() - names):
        raise ValueError("Unexpected log container.")
    identifiers.extend(logs)
    identifiers.extend(names)
    identifiers.extend(pod["name"] for pod in data.get("pods", []))
    identifiers.extend(row["name"] for row in (data.get("metrics") or {}).get("containers", []))
    identifiers.extend(error["container"] for error in data["collection_errors"] if error.get("container"))
    if not all(safe_identifier(identifier) for identifier in identifiers):
        raise ValueError("Unsafe evidence identifier.")


def sanitize_evidence(kind: str, evidence: dict, *, destination: Literal["llm", "api"],
                      target: dict[str, str]) -> dict | None:
    """Return destination-safe evidence, or withhold the observation on failure."""
    try:
        if destination not in ("llm", "api") or type(evidence) is not dict:
            return None
        schema = SCHEMAS[kind]
        metrics = evidence.get("metrics")
        if kind in {"get_metrics", "pod_report"} and type(metrics) is dict:
            containers = metrics.get("containers")
            if type(containers) is list and len(containers) > MAX_CONTAINERS:
                errors = evidence["collection_errors"]
                if type(errors) is not list or len(errors) > 2 * MAX_CONTAINERS:
                    return None
                evidence = {
                    **evidence, "metrics": {**metrics, "containers": containers[:MAX_CONTAINERS]},
                    "collection_errors": [*errors, {"operation": "metrics", "error_type": "CollectionLimit"}],
                }
        data = schema.model_validate(evidence).model_dump(exclude_unset=True)
        limit = MAX_OBSERVATION_BYTES if destination == "llm" else MAX_REPORT_BYTES
        _minimize(data, destination)
        if len(json.dumps(data, allow_nan=False)) > limit:
            return None
        _check_identities(data, target["namespace"], target.get("pod_name"), target.get("container_name"))
        for streams in data.get("logs", {}).values():
            for content in streams.values():
                if content is None:
                    continue
                size = len(content.encode())
                # The collector can clip up to three trailing bytes of a UTF-8 character.
                if size > MAX_LOG_BYTES or (size >= MAX_LOG_BYTES - 3 and not content.endswith("\n")):
                    return None
        for error in data["collection_errors"]:
            if error.get("error_type") not in ERROR_TYPES:
                error["error_type"] = "CollectionError"
            error["message"] = ERROR_MESSAGES.get(error["error_type"]) or COLLECTION_REASONS.get(
                error.get("status"), "Kubernetes request failed",
            )
        result = _sanitize_values(data)
        schema.model_validate(result)
        return result if len(json.dumps(result, allow_nan=False)) <= limit else None
    except Exception:
        # Validation and detector errors can carry raw input. Release a fixed outcome.
        return None
