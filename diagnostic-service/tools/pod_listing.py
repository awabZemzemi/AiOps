"""Bounded, read-only discovery of Pods in one namespace."""

from evidence_common import PodEvidenceError, collection_error, validate_namespace
from kubernetes_client import API_ERRORS, REQUEST_TIMEOUT, kubernetes_clients

POD_PAGE_SIZE = 50
MAX_PODS = 100
MAX_PAGES = 5


def list_pods(namespace: str, *, core_api=None) -> dict:
    """Return Pod summaries, following pagination within fixed collection limits.

    Readiness is the reported Pod Ready condition, or None when unknown.
    Restarts sum reported regular/init/ephemeral container counts, or are None
    when no container statuses are available. A namespace with no Pods returns [].
    Unreadable listings raise PodEvidenceError. A failure after collecting Pods
    preserves partial evidence and records a safe collection error.
    """
    validate_namespace(namespace)
    if core_api is None:
        with kubernetes_clients() as (core_api, _):
            return list_pods(namespace, core_api=core_api)

    result = {"namespace": namespace, "pods": [], "truncated": False, "collection_errors": []}
    continuation = None
    for _ in range(MAX_PAGES):
        try:
            response = core_api.list_namespaced_pod(
                namespace=namespace, limit=POD_PAGE_SIZE,
                _continue=continuation, _request_timeout=REQUEST_TIMEOUT,
            )
        except API_ERRORS as error:
            status = getattr(error, "status", None)
            if not result["pods"]:
                detail = {
                    401: "authentication required", 403: "access forbidden",
                    404: "resource unavailable", 429: "rate limit exceeded",
                }.get(status, "API request failed")
                raise PodEvidenceError(f"Cannot list Pods in {namespace}: {detail}", status) from error
            result["truncated"] = True
            result["collection_errors"].append(collection_error("pods", error))
            return result

        items = response.items or []
        remaining = MAX_PODS - len(result["pods"])
        for pod in items[:remaining]:
            status = pod.status
            ready_condition = next(
                (condition.status for condition in (status.conditions or [])
                 if condition.type == "Ready"), None,
            ) if status else None
            container_statuses = (
                (status.container_statuses or [])
                + (status.init_container_statuses or [])
                + (status.ephemeral_container_statuses or [])
            ) if status else []
            result["pods"].append({
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": status.phase if status else None,
                "ready": {"True": True, "False": False}.get(ready_condition),
                "restart_count": sum(item.restart_count for item in container_statuses)
                if container_statuses else None,
            })

        continuation = response.metadata._continue if response.metadata else None
        if len(items) > remaining:
            break  # Defensive local cap even if the server ignores limit.
        if not continuation:
            return result
        if len(result["pods"]) >= MAX_PODS:
            break

    result["truncated"] = True
    result["collection_errors"].append({
        "operation": "pods", "status": None, "error_type": "CollectionLimit",
        "message": f"Pod listing limited to {MAX_PODS} Pods or {MAX_PAGES} pages; listing is incomplete.",
    })
    return result
