"""Expose namespace-scoped Pod discovery to LangChain."""

import json

from kubernetes.config.config_exception import ConfigException
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from urllib3.exceptions import HTTPError

from ..state import AgentState
from . import pod_info  # Reuse the existing standalone-service import setup.
from evidence_common import PodEvidenceError
from tools.pod_listing import list_pods as collect_pods


@tool
def list_pods(runtime: ToolRuntime[None, AgentState]) -> ToolMessage:
    """Discover actual Pod names and status summaries in the application's namespace.

    Use when the user does not know the exact Pod name, a Pod lookup returned
    Not Found, or available Pods need to be inspected. Returns bounded names,
    namespaces, phases, readiness, and restart counts; incomplete listings are
    marked. Listing does not select or confirm the intended target. Use
    get_pod_info for an exact candidate afterward; ask the user if ambiguous.
    """
    if not runtime.tool_call_id:
        raise ValueError("list_pods requires a runtime tool_call_id.")

    namespace = runtime.state.get("namespace")
    message_status = "error"
    try:
        evidence = collect_pods(namespace)
    except ValueError:
        observation = {"status": "invalid_input", "message": "Application state must contain a non-empty namespace."}
    except PodEvidenceError as error:
        failures = {
            401: ("authentication_error", "Kubernetes authentication failed; available Pods are unknown."),
            403: ("permission_denied", "Permission to list Pods was denied; available Pods are unknown."),
            404: ("not_found", "The Kubernetes listing resource was not found; available Pods are unknown."),
            429: ("rate_limited", "Kubernetes API rate limit exceeded; available Pods are unknown."),
        }
        if error.status in failures:
            status, message = failures[error.status]
        elif isinstance(error.__cause__, ConfigException):
            status, message = "configuration_error", "Cannot load Kubernetes configuration; available Pods are unknown."
        elif isinstance(error.__cause__, (HTTPError, OSError)):
            status, message = "connection_error", "Cannot connect to the Kubernetes API; available Pods are unknown."
        else:
            status, message = "api_error", "Kubernetes API request failed; available Pods are unknown."
        observation = {"status": status, "http_status": error.status, "message": message}
    except (HTTPError, OSError):
        observation = {"status": "connection_error", "message": "Cannot connect to the Kubernetes API; available Pods are unknown."}
    else:
        observation = {"status": "partial" if evidence["truncated"] else "listed", "evidence": evidence}
        message_status = "success"

    observation["namespace"] = namespace
    return ToolMessage(
        content=json.dumps(observation), name="list_pods",
        tool_call_id=runtime.tool_call_id, status=message_status,
    )
