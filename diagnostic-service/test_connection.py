"""Manual connection check using the same explicit identity as all collectors."""
from evidence_common import PodEvidenceError
from kubernetes_client import API_ERRORS, kubernetes_clients

if __name__ == "__main__":
    try:
        with kubernetes_clients() as (core, _):
            pods = core.list_namespaced_pod("aiops", _request_timeout=(3, 10))
            for pod in pods.items:
                print(pod.metadata.name)
    except (PodEvidenceError, *API_ERRORS):
        raise SystemExit("Kubernetes diagnostic credentials are unavailable; check configuration and renewal.")
