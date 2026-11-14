"""Safe observations and collection errors shared by diagnostic adapters."""

import json

from kubernetes.config.config_exception import ConfigException
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from urllib3.exceptions import HTTPError

from evidence_common import PodEvidenceError
from kubernetes_client import API_ERRORS

COLLECTOR_ERRORS = (PodEvidenceError, ValueError, *API_ERRORS)


def observation_message(runtime: ToolRuntime, name: str, namespace: str,
                        observation: dict, *, pod_name: str | None = None,
                        error: bool = False) -> ToolMessage:
    if not runtime.tool_call_id:
        raise ValueError(f"{name} requires a runtime tool_call_id.")
    observation = {**observation, "namespace": namespace}
    if pod_name is not None:
        observation["pod_name"] = pod_name
    return ToolMessage(
        content=json.dumps(observation),
        name=name, tool_call_id=runtime.tool_call_id,
        status="error" if error else "success",
    )


def collector_failure(runtime: ToolRuntime, name: str, namespace: str,
                      error: Exception, *, pod_name: str | None = None) -> ToolMessage:
    """Preserve error categories without copying raw exception details."""
    http_status = getattr(error, "status", None)
    failures = {
        401: ("authentication_error", "Kubernetes authentication failed."),
        403: ("permission_denied", "Permission to read diagnostic evidence was denied."),
        404: ("not_found", "The requested Kubernetes diagnostic resource was not found."),
        429: ("rate_limited", "Kubernetes API rate limit exceeded."),
    }
    if isinstance(error, ValueError):
        status, message = "invalid_input", "Invalid diagnostic tool arguments."
    elif http_status in failures:
        status, message = failures[http_status]
    elif isinstance(error.__cause__, ConfigException):
        status, message = "configuration_error", "Cannot load Kubernetes configuration."
    elif isinstance(error, (HTTPError, OSError)) or isinstance(error.__cause__, (HTTPError, OSError)):
        status, message = "connection_error", "Cannot connect to the Kubernetes API."
    else:
        status, message = "api_error", "Kubernetes API request failed."
    return observation_message(runtime, name, namespace, {
        "status": status, "http_status": http_status, "message": message,
    }, pod_name=pod_name, error=True)


def evidence_message(runtime: ToolRuntime, name: str, namespace: str, pod_name: str,
                     evidence: dict, *, has_evidence: bool) -> ToolMessage:
    """Keep collector errors alongside partial evidence, including empty logs."""
    errors = evidence.get("collection_errors", [])
    failed = bool(errors) and not has_evidence
    status = "collection_error" if failed else "partial" if errors else "collected"
    return observation_message(runtime, name, namespace, {
        "status": status, "evidence": evidence,
    }, pod_name=pod_name, error=failed)
