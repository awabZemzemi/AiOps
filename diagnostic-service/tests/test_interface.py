import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kubernetes_client
import main
from diagnostics import PodEvidenceError


class InterfaceTests(unittest.TestCase):
    def test_clients_use_current_context_without_rewriting_kubeconfig(self):
        configuration = Mock()
        api_client = Mock()
        with patch.object(kubernetes_client.client, "Configuration", return_value=configuration), \
             patch.object(kubernetes_client.config, "load_kube_config") as load, \
             patch.object(kubernetes_client.client, "ApiClient") as factory, \
             patch.object(kubernetes_client.client, "CoreV1Api") as core, \
             patch.object(kubernetes_client.client, "CustomObjectsApi") as metrics:
            factory.return_value.__enter__.return_value = api_client
            with kubernetes_client.kubernetes_clients() as clients:
                self.assertEqual(clients, (core.return_value, metrics.return_value))
            load.assert_called_once_with(client_configuration=configuration, persist_config=False)
            self.assertEqual(configuration.retries, 0)
            core.assert_called_once_with(api_client)
            metrics.assert_called_once_with(api_client)
            factory.return_value.__exit__.assert_called_once()

    def test_cli_prints_json(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["main.py", "demo", "-n", "aiops"]), \
             patch.object(main, "collect_pod_evidence", return_value={"metrics": None}) as collect, \
             contextlib.redirect_stdout(output):
            self.assertEqual(main.main(), 0)
        self.assertEqual(json.loads(output.getvalue()), {"metrics": None})
        collect.assert_called_once_with("aiops", "demo", tail_lines=100)

    def test_cli_target_error_has_nonzero_exit(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["main.py", "missing", "-n", "aiops"]), \
             patch.object(main, "collect_pod_evidence", side_effect=PodEvidenceError("Pod not found", 404)), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(main.main(), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Pod not found", errors.getvalue())
