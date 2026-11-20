"""Create clients with explicitly selected credentials; never use personal defaults."""
from contextlib import contextmanager
import os
from pathlib import Path

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError

from diagnostic_service.evidence_common import PodEvidenceError

REQUEST_TIMEOUT = (3, 10)
API_ERRORS = (ApiException, HTTPError, OSError)


@contextmanager
def kubernetes_clients():
    configuration = client.Configuration()
    try:
        mode = os.environ.get("AIOPS_KUBERNETES_MODE", "")
        if mode == "kubeconfig":
            path = os.environ.get("AIOPS_KUBECONFIG_PATH", "")
            if not path or not Path(path).is_file():
                raise ConfigException("Explicit kubeconfig file required.")
            config.load_kube_config(config_file=path, client_configuration=configuration,
                                    persist_config=False)
        elif mode == "incluster":
            config.load_incluster_config(client_configuration=configuration)
        else:
            raise ConfigException("Explicit Kubernetes credential mode required.")
        if (not configuration.host.startswith("https://") or not configuration.verify_ssl
                or not configuration.ssl_ca_cert or not Path(configuration.ssl_ca_cert).is_file()):
            raise ConfigException("Verified HTTPS with a CA certificate is required.")
    except Exception:
        # Loader exceptions can contain sensitive details. Preserve only the category.
        error = ConfigException("Restricted Kubernetes credential configuration failed.")
        raise PodEvidenceError(
            "Cannot load kubeconfig or in-cluster credentials; check AIOPS_KUBERNETES_MODE "
            "and AIOPS_KUBECONFIG_PATH; renew expired local credentials."
        ) from error
    configuration.retries = 0
    with client.ApiClient(configuration) as api_client:
        yield client.CoreV1Api(api_client), client.CustomObjectsApi(api_client)
