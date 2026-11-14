"""LangChain adapter for the existing bounded container log collector."""

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from pydantic import Field

from ._common import COLLECTOR_ERRORS, collector_failure, evidence_message
from tools.container_logs import DEFAULT_LOG_LINES, MAX_LOG_LINES, collect_container_logs


@tool
def get_logs(
    namespace: str,
    pod_name: str,
    container_name: Annotated[str, Field(min_length=1, max_length=63, description="Exact container name from get_pod_info evidence.")],
    runtime: ToolRuntime,
    tail_lines: Annotated[int, Field(strict=True, ge=1, le=MAX_LOG_LINES, description="Maximum recent lines per current/previous log stream.")] = DEFAULT_LOG_LINES,
) -> ToolMessage:
    """Read recent current and previous logs for a container in the supplied Pod.

    Use to investigate application errors or crashes after get_pod_info has
    verified the Pod and reported its container names. Supply the namespace,
    exact Pod name, and container name. Defaults to 100 lines; allows up to
    1000 lines and 16 KiB per stream. Missing previous logs are reported as a
    partial collection error. Does not follow logs or modify resources.
    """
    try:
        evidence = collect_container_logs(namespace, pod_name, container_name, tail_lines=tail_lines)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_logs", namespace, error, pod_name=pod_name)
    streams = evidence["logs"][container_name]
    return evidence_message(runtime, "get_logs", namespace, pod_name, evidence,
                            has_evidence=any(value is not None for value in streams.values()))
