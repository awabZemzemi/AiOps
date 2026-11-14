"""LangChain adapter for the existing bounded Kubernetes event collector."""

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage

from ._common import COLLECTOR_ERRORS, collector_failure, evidence_message
from tools.kubernetes_events import collect_kubernetes_events


@tool
def get_events(namespace: str, pod_name: str, runtime: ToolRuntime) -> ToolMessage:
    """Read up to 100 Kubernetes events associated with the supplied Pod.

    Use to investigate scheduling, image pulls, probes, or restart problems.
    Supply a namespace and exact Pod name from get_pod_info evidence.
    Events are filtered by Pod kind and exact name.
    Without a stored UID, older instances with the same name may be included.
    An empty result is valid; API errors and truncation are reported separately.
    This tool is read-only.
    """
    try:
        evidence = collect_kubernetes_events(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_events", namespace, error, pod_name=pod_name)
    return evidence_message(runtime, "get_events", namespace, pod_name, evidence,
                            has_evidence=bool(evidence["events"]))
