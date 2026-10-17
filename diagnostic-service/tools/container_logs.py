"""Independent, bounded container log evidence tool."""
from evidence_common import collection_error, validate_target
from kubernetes_client import API_ERRORS, REQUEST_TIMEOUT, kubernetes_clients

DEFAULT_LOG_LINES = 100
MAX_LOG_LINES = 1000
MAX_LOG_BYTES = 16 * 1024  # Per current/previous stream.


def validate_tail_lines(tail_lines):
    if isinstance(tail_lines, bool) or not isinstance(tail_lines, int) or not 1 <= tail_lines <= MAX_LOG_LINES:
        raise ValueError(f"tail_lines must be an integer between 1 and {MAX_LOG_LINES}")


def collect_container_logs(namespace: str, pod_name: str, container_name: str, *,
                           tail_lines: int = DEFAULT_LOG_LINES, core_api=None) -> dict:
    """Return this container's current/previous logs and safe partial errors.

    Uses current kubeconfig unless core_api is supplied. Never prints or follows logs.
    """
    validate_target(namespace, pod_name)
    validate_tail_lines(tail_lines)
    if not isinstance(container_name, str) or not container_name.strip():
        raise ValueError("container_name must be non-empty")
    if core_api is None:
        with kubernetes_clients() as (core_api, _):
            return collect_container_logs(namespace, pod_name, container_name,
                                          tail_lines=tail_lines, core_api=core_api)
    errors = []
    streams = {"current": None, "previous": None}
    for stream in streams:
        try:
            logs = core_api.read_namespaced_pod_log(
                name=pod_name, namespace=namespace, container=container_name,
                previous=stream == "previous", timestamps=True, tail_lines=tail_lines,
                limit_bytes=MAX_LOG_BYTES, follow=False, _request_timeout=REQUEST_TIMEOUT,
            )
            # Defensive local cap as well as the server-side byte limit.
            streams[stream] = logs.encode("utf-8")[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
        except API_ERRORS as error:
            errors.append(collection_error(f"logs.{stream}", error, container_name))
    return {"logs": {container_name: streams}, "collection_errors": errors}

