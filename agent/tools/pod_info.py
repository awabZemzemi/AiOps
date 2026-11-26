"""LangChain adapter for Pod and container metadata collection."""

from langchain.tools import ToolRuntime, tool
from agent.authorization import AuthorizationContext
from langchain_core.messages import ToolMessage

from ._common import COLLECTOR_ERRORS, collector_failure, observation_message, require_tool_namespace
from diagnostic_service.tools.pod_information import collect_pod_information


@tool
def get_pod_info(namespace: str, pod_name: str, runtime: ToolRuntime[AuthorizationContext]) -> ToolMessage:
    """Read a Pod's metadata, phase, conditions, and container details.

    Supply a namespace and exact Pod name. A successful read verifies existence;
    a not-found result does not. Use list_pods if the exact name is unknown.
    Does not collect logs, events, or metrics, or modify resources.
    """
    require_tool_namespace(runtime, namespace)
    try:
        evidence = collect_pod_information(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_pod_info", namespace, error, pod_name=pod_name)

    return observation_message(runtime, "get_pod_info", namespace, {"evidence": evidence}, pod_name=pod_name)
