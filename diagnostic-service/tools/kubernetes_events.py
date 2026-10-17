"""Independent Kubernetes event evidence tool."""
from evidence_common import collection_error, validate_target
from kubernetes_client import API_ERRORS, REQUEST_TIMEOUT, kubernetes_clients
from models import event_info

MAX_EVENTS = 100


def collect_kubernetes_events(namespace: str, pod_name: str, *, pod_uid: str | None = None,
                              core_api=None) -> dict:
    """Return bounded related events; pass pod_uid to exclude earlier Pod instances.

    Without a UID, filtering falls back to the Pod name. No other tool is required.
    """
    validate_target(namespace, pod_name)
    if core_api is None:
        with kubernetes_clients() as (core_api, _):
            return collect_kubernetes_events(namespace, pod_name, pod_uid=pod_uid, core_api=core_api)
    result = {"events": [], "collection_errors": []}
    selector = f"involvedObject.uid={pod_uid}" if pod_uid else f"involvedObject.name={pod_name}"
    try:
        response = core_api.list_namespaced_event(
            namespace=namespace, field_selector=f"involvedObject.kind=Pod,{selector}",
            limit=MAX_EVENTS, _request_timeout=REQUEST_TIMEOUT,
        )
        # Check identity again so a recreated Pod cannot inherit another Pod's events.
        result["events"] = [event_info(event) for event in (response.items or [])[:MAX_EVENTS]
                            if event.involved_object and
                            ((event.involved_object.uid == pod_uid) if pod_uid else
                             (event.involved_object.name == pod_name))]
        if response.metadata and response.metadata._continue:
            result["collection_errors"].append({
                "operation": "events", "status": None, "error_type": "CollectionLimit",
                "message": f"Event collection limited to {MAX_EVENTS} entries",
            })
    except API_ERRORS as error:
        result["collection_errors"].append(collection_error("events", error))

    return result
