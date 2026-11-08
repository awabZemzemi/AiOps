"""LangChain adapter for the existing CPU and memory metrics collector."""

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage

from ..state import AgentState
from ._common import COLLECTOR_ERRORS, collector_failure, evidence_message, verified_target
from models import MAX_TEXT_CHARS, text
from tools.resource_metrics import collect_resource_metrics

MAX_METRIC_CONTAINERS = 100


@tool
def get_metrics(runtime: ToolRuntime[None, AgentState]) -> ToolMessage:
    """Read current per-container CPU and memory usage for the verified target Pod.

    Use to investigate resource usage after get_pod_info verifies a target.
    Namespace and Pod name come only from application state. Returns Kubernetes
    quantity strings, sample timestamp, and window, bounded to 100 containers.
    Reports unavailable Metrics Server, missing samples, permissions, and other
    errors without inventing measurements. This is read-only and does not change
    the target or collect historical usage.
    """
    target = verified_target(runtime, "get_metrics")
    if isinstance(target, ToolMessage):
        return target
    namespace, pod_name = target
    try:
        evidence = collect_resource_metrics(namespace, pod_name)
    except COLLECTOR_ERRORS as error:
        return collector_failure(runtime, "get_metrics", error)

    metrics = evidence["metrics"]
    if metrics is not None:
        # Bound the LLM-facing observation without altering the Sprint 2 collector.
        containers = metrics["containers"]
        bounded = {
            "timestamp": text(metrics["timestamp"]), "window": text(metrics["window"]),
            "containers": [{key: text(container.get(key)) for key in ("name", "cpu", "memory")}
                           for container in containers[:MAX_METRIC_CONTAINERS]],
        }
        values = [metrics["timestamp"], metrics["window"]]
        values.extend(container.get(key) for container in containers[:MAX_METRIC_CONTAINERS]
                      for key in ("name", "cpu", "memory"))
        truncated = len(containers) > MAX_METRIC_CONTAINERS or any(
            isinstance(value, str) and len(value) > MAX_TEXT_CHARS for value in values
        )
        evidence = {**evidence, "metrics": bounded, "collection_errors": list(evidence["collection_errors"])}
        if truncated:
            evidence["collection_errors"].append({
                "operation": "metrics", "status": None, "error_type": "CollectionLimit",
                "message": f"Metrics observation limited to {MAX_METRIC_CONTAINERS} containers and {MAX_TEXT_CHARS} characters per field.",
            })
    return evidence_message(runtime, "get_metrics", evidence, has_evidence=metrics is not None)
