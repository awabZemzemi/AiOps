"""LangChain adapter for the existing CPU and memory metrics collector."""

from langchain.tools import ToolRuntime, tool
from agent.authorization import AuthorizationContext
from langchain_core.messages import ToolMessage

from ._common import COLLECTOR_ERRORS, collector_failure, observation_message, require_tool_namespace
from diagnostic_service.tools.resource_metrics import collect_resource_metrics


@tool
def get_metrics(namespace: str, pod_name: str, runtime: ToolRuntime[AuthorizationContext]) -> ToolMessage:
    """Read current per-container CPU and memory usage for the supplied Pod.

    Use to investigate resource usage after get_pod_info verifies a target.
    Supply a namespace and exact Pod name. Returns Kubernetes
    quantity strings, sample timestamp, and window, bounded to 100 containers.
    Reports unavailable Metrics Server, missing samples, permissions, and other
    errors without inventing measurements. This is read-only and does not
    collect historical usage.
    """
    require_tool_namespace(runtime, namespace)
    try:
        evidence = collect_resource_metrics(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_metrics", namespace, error, pod_name=pod_name)

    return observation_message(runtime, "get_metrics", namespace, {"evidence": evidence}, pod_name=pod_name)
