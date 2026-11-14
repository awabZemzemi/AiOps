"""Expose namespace-scoped Pod discovery to LangChain."""

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage

from ._common import COLLECTOR_ERRORS, collector_failure, observation_message
from tools.pod_listing import list_pods as collect_pods


@tool
def list_pods(namespace: str, runtime: ToolRuntime) -> ToolMessage:
    """Discover actual Pod names and status summaries in the supplied namespace.

    Use when the exact Pod name is unknown, a lookup returned Not Found, or
    available Pods need inspection. Returns bounded names, namespaces, phases,
    readiness, and restart counts; incomplete listings are marked. Use
    get_pod_info for an exact candidate afterward; ask the user if ambiguous.
    This tool is read-only.
    """
    try:
        evidence = collect_pods(namespace)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "list_pods", namespace, error)
    return observation_message(runtime, "list_pods", namespace, {
        "status": "partial" if evidence["truncated"] else "listed", "evidence": evidence,
    })
