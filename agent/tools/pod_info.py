"""LangChain adapter for Pod and container metadata collection."""

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage

from ._common import COLLECTOR_ERRORS, collector_failure, observation_message
from tools.pod_information import collect_pod_information


@tool
def get_pod_info(namespace: str, pod_name: str, runtime: ToolRuntime) -> ToolMessage:
    """Read a Pod's metadata, phase, conditions, and container details.

    Supply a namespace and exact Pod name. A successful read verifies existence;
    a not-found result does not. Use list_pods if the exact name is unknown.
    Does not collect logs, events, or metrics, or modify resources.
    """
    try:
        evidence = collect_pod_information(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_pod_info", namespace, error, pod_name=pod_name)

    pod = evidence["pod"]
    if pod.get("name") != pod_name or pod.get("namespace") != namespace:
        return observation_message(runtime, "get_pod_info", namespace, {
            "status": "invalid_evidence",
            "message": "Collected Pod identity does not match the request; existence is not verified.",
        }, pod_name=pod_name, error=True)
    return observation_message(runtime, "get_pod_info", namespace, {
        "status": "found", "evidence": evidence,
    }, pod_name=pod_name)
