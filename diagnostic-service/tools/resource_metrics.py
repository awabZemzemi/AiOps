"""Independent optional resource metrics evidence tool."""
from evidence_common import collection_error, validate_target
from kubernetes_client import API_ERRORS, REQUEST_TIMEOUT, kubernetes_clients


def collect_resource_metrics(namespace: str, pod_name: str, *, pod_uid: str | None = None,
                             metrics_api=None) -> dict:
    """Return current CPU/memory usage or null plus safe partial errors.

    Pass pod_uid to verify identity when the metrics response includes a UID.
    """
    validate_target(namespace, pod_name)
    if metrics_api is None:
        with kubernetes_clients() as (_, metrics_api):
            return collect_resource_metrics(namespace, pod_name, pod_uid=pod_uid, metrics_api=metrics_api)
    result = {"metrics": None, "collection_errors": []}
    try:
        data = metrics_api.get_namespaced_custom_object(
            group="metrics.k8s.io", version="v1beta1", namespace=namespace,
            plural="pods", name=pod_name, _request_timeout=REQUEST_TIMEOUT,
        )
        expected_uid = pod_uid
        metrics_uid = data.get("metadata", {}).get("uid")
        if expected_uid and metrics_uid and expected_uid != metrics_uid:
            result["collection_errors"].append({
                "operation": "metrics", "status": None, "error_type": "PodIdentityChanged",
                "message": "Metrics refer to a different Pod UID",
            })
            return result
        result["metrics"] = {
            "timestamp": data.get("timestamp"), "window": data.get("window"),
            "containers": [{"name": c.get("name"),
                            "cpu": c.get("usage", {}).get("cpu"),
                            "memory": c.get("usage", {}).get("memory")}
                           for c in data.get("containers", [])],
        }
    except API_ERRORS as error:
        result["collection_errors"].append(collection_error("metrics", error))

    return result
