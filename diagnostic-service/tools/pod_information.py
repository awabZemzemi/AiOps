"""Independent Pod and container metadata evidence tool."""
from evidence_common import PodEvidenceError, validate_target
from kubernetes_client import API_ERRORS, REQUEST_TIMEOUT, kubernetes_clients
from models import container_info, pod_info


def collect_pod_information(namespace: str, pod_name: str, *, core_api=None) -> dict:
    """Return safe Pod/container details; raise PodEvidenceError if unreadable."""
    validate_target(namespace, pod_name)
    if core_api is None:
        with kubernetes_clients() as (core_api, _):
            return collect_pod_information(namespace, pod_name, core_api=core_api)
    result = {"pod": {}, "containers": [], "collection_errors": []}
    try:
        pod = core_api.read_namespaced_pod(
            name=pod_name, namespace=namespace, _request_timeout=REQUEST_TIMEOUT,
        )
    except API_ERRORS as error:
        status = getattr(error, "status", None)
        detail = {404: "not found", 403: "access forbidden", 401: "authentication required"}.get(
            status, "API request failed")
        raise PodEvidenceError(f"Cannot read Pod {namespace}/{pod_name}: {detail}", status) from error
    result["pod"] = pod_info(pod)
    groups = [("regular", pod.spec.containers, "container_statuses"),
              ("init", pod.spec.init_containers, "init_container_statuses"),
              ("ephemeral", pod.spec.ephemeral_containers, "ephemeral_container_statuses")]
    for container_type, containers, status_field in groups:
        statuses = {s.name: s for s in (getattr(pod.status, status_field, None) or [])}
        for container in containers or []:
            result["containers"].append(container_info(container, statuses.get(container.name), container_type))
    return result
