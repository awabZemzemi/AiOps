"""LangChain adapter for the existing bounded Kubernetes event collector."""

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage

from ..state import AgentState
from ._common import COLLECTOR_ERRORS, collector_failure, evidence_message, verified_target
from tools.kubernetes_events import collect_kubernetes_events


@tool
def get_events(runtime: ToolRuntime[None, AgentState]) -> ToolMessage:
    """Read up to 100 Kubernetes events associated with the verified target Pod.

    Use to investigate scheduling, image pulls, probes, or restart problems.
    Namespace and Pod name come only from application state; get_pod_info must
    verify a target first. Events are filtered by Pod kind and exact name.
    Without a stored UID, older instances with the same name may be included.
    An empty result is valid; API errors and truncation are reported separately.
    This tool is read-only and does not select or change the target.
    """
    target = verified_target(runtime, "get_events")
    if isinstance(target, ToolMessage):
        return target
    namespace, pod_name = target
    try:
        evidence = collect_kubernetes_events(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_events", error)
    return evidence_message(runtime, "get_events", evidence, has_evidence=bool(evidence["events"]))
