"""Safe observations and collection errors shared by diagnostic adapters."""

import json

from kubernetes.config.config_exception import ConfigException
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from urllib3.exceptions import HTTPError

from diagnostic_service.evidence_common import PodEvidenceError
from diagnostic_service.kubernetes_client import API_ERRORS
from diagnostic_service.security import sanitize_evidence
from diagnostic_service.security.sanitizer import safe_identifier
from agent.authorization import require_tool_namespace

COLLECTOR_ERRORS = (PodEvidenceError, ValueError, *API_ERRORS)
FAILURES = {
    401: ("authentication_error", "Kubernetes authentication failed."),
    403: ("permission_denied", "Permission to read diagnostic evidence was denied."),
    404: ("not_found", "The requested Kubernetes diagnostic resource was not found."),
    429: ("rate_limited", "Kubernetes API rate limit exceeded."),
}
ERROR_MESSAGES = dict(FAILURES.values()) | {
    "invalid_input": "Invalid diagnostic tool arguments.",
    "configuration_error": "Cannot load Kubernetes configuration.",
    "connection_error": "Cannot connect to the Kubernetes API.",
    "api_error": "Kubernetes API request failed.",
    "invalid_evidence": "Diagnostic evidence was withheld.",
}


def project_observation(name: str, observation: dict, target: dict[str, str]) -> tuple[dict, bool]:
    """Build the allowlisted tool envelope from an adapter result or decoded history."""
    if type(observation) is not dict:
        raise ValueError("Invalid tool observation.")
    identity = {key: target[key] for key in ("namespace", "pod_name") if key in target}
    if any(observation.get(key, value) != value or not safe_identifier(value)
           for key, value in identity.items()):
        raise ValueError("Invalid observation identity.")
    if "evidence" not in observation:
        status = observation.get("status")
        if status not in ERROR_MESSAGES:
            raise ValueError("Unknown tool error.")
        error = {"status": status, "message": ERROR_MESSAGES[status]}
        if "http_status" in observation:
            http_status = observation["http_status"]
            error["http_status"] = http_status if type(http_status) is int and 0 <= http_status <= 599 else None
        return (error if status == "invalid_evidence" else {**error, **identity}), True

    evidence = sanitize_evidence(name, observation["evidence"], destination="llm", target=target)
    if evidence is None:
        raise ValueError("Unsafe tool evidence.")
    errors = evidence["collection_errors"]
    if name == "list_pods":
        status = "partial" if evidence["truncated"] else "listed"
        failed = False
    elif name == "get_pod_info":
        status, failed = "found", False
    else:
        available = any(value is not None for streams in evidence.get("logs", {}).values()
                        for value in streams.values()) or bool(evidence.get("events")) or evidence.get("metrics") is not None
        failed = bool(errors) and not available
        status = "collection_error" if failed else "partial" if errors else "collected"
    return {"status": status, "evidence": evidence, **identity}, failed


def observation_message(runtime: ToolRuntime, name: str, namespace: str, observation: dict, *,
                        pod_name: str | None = None, container_name: str | None = None) -> ToolMessage:
    if not runtime.tool_call_id:
        raise ValueError(f"{name} requires a runtime tool_call_id.")
    target = {"namespace": namespace}
    if pod_name is not None:
        target["pod_name"] = pod_name
    if container_name is not None:
        target["container_name"] = container_name
    try:
        content, failed = project_observation(name, observation, target)
    except Exception:
        content = {"status": "invalid_evidence", "message": ERROR_MESSAGES["invalid_evidence"]}
        failed = True
    return ToolMessage(
        content=json.dumps(content),
        name=name, tool_call_id=runtime.tool_call_id,
        status="error" if failed else "success",
    )


def collector_failure(runtime: ToolRuntime, name: str, namespace: str,
                      error: Exception, *, pod_name: str | None = None) -> ToolMessage:
    """Preserve error categories without copying raw exception details."""
    http_status = getattr(error, "status", None)
    if isinstance(error, ValueError):
        status = "invalid_input"
    elif type(http_status) is int and http_status in FAILURES:
        status = FAILURES[http_status][0]
    elif isinstance(error.__cause__, ConfigException):
        status = "configuration_error"
    elif isinstance(error, (HTTPError, OSError)) or isinstance(error.__cause__, (HTTPError, OSError)):
        status = "connection_error"
    else:
        status = "api_error"
    return observation_message(runtime, name, namespace, {
        "status": status, "http_status": http_status,
    }, pod_name=pod_name)
