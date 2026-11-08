"""Adapt Sprint 2 Pod evidence collection to a state-aware LangChain tool."""

import json
from pathlib import Path
import sys
from typing import Annotated

from kubernetes.config.config_exception import ConfigException
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import Field
from urllib3.exceptions import HTTPError

from ..state import AgentState

# Sprint 2 is a standalone service with flat imports, rather than an installed
# Python package. Make its existing modules importable without changing them.
_DIAGNOSTIC_SERVICE = str(Path(__file__).resolve().parents[2] / "diagnostic-service")
if _DIAGNOSTIC_SERVICE not in sys.path:
    sys.path.insert(0, _DIAGNOSTIC_SERVICE)

from evidence_common import PodEvidenceError, validate_target
from tools.pod_information import collect_pod_information


@tool
def get_pod_info(
    pod_name: Annotated[
        str,
        Field(min_length=1, description="Exact Pod name to inspect in the application's namespace."),
    ],
    runtime: ToolRuntime[None, AgentState],
) -> Command:
    """Read a Pod's metadata, phase, conditions, and container details.

    Use this to check whether a named Pod exists or investigate its current
    status. It performs a read-only Kubernetes lookup in the application's
    namespace. A successful lookup confirms the Pod exists; a not-found result
    does not. It does not collect logs, events, or metrics, or modify resources.
    """
    if not runtime.tool_call_id:
        raise ValueError("get_pod_info requires a runtime tool_call_id.")

    namespace = runtime.state.get("namespace")
    verified_pod = None
    try:
        validate_target(namespace, pod_name)
        evidence = collect_pod_information(namespace, pod_name)
    except ValueError:
        observation = {
            "status": "invalid_input",
            "message": "Application state must contain a non-empty namespace and pod_name must be non-empty.",
        }
    except PodEvidenceError as error:
        failures = {
            404: ("not_found", "Pod was not found in the application's namespace; no target was verified."),
            401: ("authentication_error", "Kubernetes authentication failed; Pod existence is unknown."),
            403: ("permission_denied", "Permission to read the Pod was denied; Pod existence is unknown."),
            429: ("rate_limited", "Kubernetes API rate limit exceeded; Pod existence is unknown."),
        }
        if error.status in failures:
            status, message = failures[error.status]
        elif isinstance(error.__cause__, ConfigException):
            status, message = "configuration_error", "Cannot load Kubernetes configuration; Pod existence is unknown."
        elif isinstance(error.__cause__, (HTTPError, OSError)):
            status, message = "connection_error", "Cannot connect to the Kubernetes API; Pod existence is unknown."
        else:
            status, message = "api_error", "Kubernetes API request failed; Pod existence is unknown."
        # Return safe observations, never raw API exception bodies or credentials.
        observation = {"status": status, "http_status": error.status, "message": message}
    except (HTTPError, OSError):
        observation = {
            "status": "connection_error",
            "message": "Cannot connect to the Kubernetes API; Pod existence is unknown.",
        }
    else:
        pod = evidence["pod"]
        if pod.get("name") != pod_name or pod.get("namespace") != namespace:
            observation = {
                "status": "invalid_evidence",
                "message": "Collected Pod identity does not match the request; no target was verified.",
            }
        else:
            verified_pod = pod["name"]
            observation = {"status": "found", "evidence": evidence}

    observation.update({"namespace": namespace, "pod_name": pod_name})
    updates = {
        "messages": [
            ToolMessage(
                content=json.dumps(observation),
                name="get_pod_info",
                tool_call_id=runtime.tool_call_id,
                status="success" if verified_pod is not None else "error",
            )
        ]
    }
    if verified_pod is not None and runtime.state["target_pod"] is None:
        updates["target_pod"] = verified_pod
    return Command(update=updates)
