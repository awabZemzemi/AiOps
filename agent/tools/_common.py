"""State checks and safe observations shared by verified-target adapters."""

import json

from kubernetes.config.config_exception import ConfigException
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from urllib3.exceptions import HTTPError

from ..state import AgentState
from . import pod_info  # Reuse the existing standalone-service import setup.
from evidence_common import PodEvidenceError, validate_target
from kubernetes_client import API_ERRORS

COLLECTOR_ERRORS = (PodEvidenceError, ValueError, *API_ERRORS)


def observation_message(runtime: ToolRuntime[None, AgentState], name: str,
                        observation: dict, *, error: bool = False) -> ToolMessage:
    if not runtime.tool_call_id:
        raise ValueError(f"{name} requires a runtime tool_call_id.")
    return ToolMessage(
        content=json.dumps({
            **observation,
            "namespace": runtime.state.get("namespace"),
            "pod_name": runtime.state.get("target_pod"),
        }),
        name=name, tool_call_id=runtime.tool_call_id,
        status="error" if error else "success",
    )


def verified_target(runtime: ToolRuntime[None, AgentState], name: str) -> tuple[str, str] | ToolMessage:
    """Reject unverified/invalid state before a collector can open clients."""
    if not runtime.tool_call_id:
        raise ValueError(f"{name} requires a runtime tool_call_id.")
    namespace = runtime.state.get("namespace")
    target = runtime.state.get("target_pod")
    if target is None:
        return observation_message(runtime, name, {
            "status": "no_verified_target",
            "message": "No Pod has been verified. Call get_pod_info for an exact candidate first.",
        }, error=True)
    try:
        validate_target(namespace, target)
    except ValueError:
        return observation_message(runtime, name, {
            "status": "invalid_state",
            "message": "Application state must contain a non-empty namespace and verified target_pod.",
        }, error=True)
    return namespace, target


def collector_failure(runtime: ToolRuntime[None, AgentState], name: str, error: Exception) -> ToolMessage:
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
    return observation_message(runtime, name, {
        "status": status, "http_status": http_status, "message": message,
    }, error=True)


def evidence_message(runtime: ToolRuntime[None, AgentState], name: str,
                     evidence: dict, *, has_evidence: bool) -> ToolMessage:
    """Keep collector errors alongside partial evidence, including empty logs."""
    errors = evidence.get("collection_errors", [])
    failed = bool(errors) and not has_evidence
    status = "collection_error" if failed else "partial" if errors else "collected"
    return observation_message(runtime, name, {
        "status": status, "evidence": evidence,
    }, error=failed)
