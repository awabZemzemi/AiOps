"""Create official Kubernetes clients using the current kubeconfig context."""
from contextlib import contextmanager

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from evidence_common import PodEvidenceError
from urllib3.exceptions import HTTPError

REQUEST_TIMEOUT = (3, 10)  # Connection timeout, then read timeout (seconds).
API_ERRORS = (ApiException, HTTPError, OSError)


@contextmanager
def kubernetes_clients():
    # Keep configuration local to this collector; do not rewrite kubeconfig.
    configuration = client.Configuration()
    try:
        config.load_kube_config(client_configuration=configuration, persist_config=False)
    except ConfigException as error:
        raise PodEvidenceError("Cannot load kubeconfig; check KUBECONFIG and the current context") from error
    configuration.retries = 0  # Do not multiply bounded requests with transport retries.
    with client.ApiClient(configuration) as api_client:
        yield client.CoreV1Api(api_client), client.CustomObjectsApi(api_client)
