"""High-level orchestration of independent, read-only evidence tools."""
from datetime import datetime, timezone

# Keep existing constant/error imports working for callers and the original tests.
from tools.container_logs import (
    DEFAULT_LOG_LINES, MAX_LOG_BYTES, MAX_LOG_LINES, collect_container_logs, validate_tail_lines,
)
from evidence_common import PodEvidenceError, validate_target
from kubernetes_client import kubernetes_clients
from tools.kubernetes_events import MAX_EVENTS, collect_kubernetes_events
from models import EvidenceReport
from tools.pod_information import collect_pod_information
from tools.resource_metrics import collect_resource_metrics


def _collect(namespace, pod_name, tail_lines, core_api, metrics_api):
    report: EvidenceReport = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target": {"namespace": namespace, "pod": pod_name},
        "pod": {}, "containers": [], "logs": {}, "events": [], "metrics": None,
        "collection_errors": [],
    }
    information = collect_pod_information(namespace, pod_name, core_api=core_api)
    report["pod"] = information["pod"]
    report["containers"] = information["containers"]
    report["collection_errors"].extend(information["collection_errors"])
    for container in report["containers"]:
        logs = collect_container_logs(namespace, pod_name, container["name"],
                                      tail_lines=tail_lines, core_api=core_api)
        report["logs"].update(logs["logs"])
        report["collection_errors"].extend(logs["collection_errors"])
    events = collect_kubernetes_events(namespace, pod_name, pod_uid=report["pod"]["uid"], core_api=core_api)
    report["events"] = events["events"]
    report["collection_errors"].extend(events["collection_errors"])
    metrics = collect_resource_metrics(namespace, pod_name, pod_uid=report["pod"]["uid"], metrics_api=metrics_api)
    report["metrics"] = metrics["metrics"]
    report["collection_errors"].extend(metrics["collection_errors"])
    return report


def collect_pod_evidence(namespace: str, pod_name: str, *, tail_lines: int = DEFAULT_LOG_LINES,
                         core_api=None, metrics_api=None) -> dict:
    """Combine tool results into the existing report using one shared client session.

    Optional evidence failures are recorded. Failure to read the target raises
    PodEvidenceError. Inject both clients for offline tests, or use current kubeconfig.
    """
    validate_target(namespace, pod_name)
    validate_tail_lines(tail_lines)
    if (core_api is None) != (metrics_api is None):
        raise ValueError("Inject both core_api and metrics_api, or neither")
    if core_api is not None:
        return _collect(namespace, pod_name, tail_lines, core_api, metrics_api)
    with kubernetes_clients() as (core_api, metrics_api):
        return _collect(namespace, pod_name, tail_lines, core_api, metrics_api)
