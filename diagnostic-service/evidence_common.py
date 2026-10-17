"""Shared input validation and safe collection errors; no Kubernetes calls."""

class PodEvidenceError(RuntimeError):
    """The target Pod could not be read, or kubeconfig could not be loaded."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def collection_error(operation, error, container=None):
    status = getattr(error, "status", None)
    # Never copy raw API bodies, headers, or exception messages into evidence.
    reasons = {400: "Request unavailable", 401: "Authentication required",
               403: "Access forbidden", 404: "Resource unavailable",
               429: "API rate limit exceeded"}
    entry = {"operation": operation, "status": status,
             "error_type": type(error).__name__,
             "message": reasons.get(status, "Kubernetes request failed")}
    if container is not None:
        entry["container"] = container
    return entry



def validate_target(namespace, pod_name):
    if (not isinstance(namespace, str) or not namespace.strip()
            or not isinstance(pod_name, str) or not pod_name.strip()):
        raise ValueError("namespace and pod_name must be non-empty")
