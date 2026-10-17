"""Exercise each tool directly, without the orchestrator or a live cluster."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from tools import container_logs
import kubernetes_client
from tools import kubernetes_events
from tools import pod_information
from tools import resource_metrics
from evidence_common import PodEvidenceError
from test_diagnostics import make_pod, event


class IndependentToolTests(unittest.TestCase):
    def setUp(self):
        self.core = Mock(spec=client.CoreV1Api)
        self.metrics = Mock(spec=client.CustomObjectsApi)
        self.core.read_namespaced_pod.return_value = make_pod()
        self.core.read_namespaced_pod_log.return_value = "2026-10-17T00:00:00Z started\n"
        self.core.list_namespaced_event.return_value = client.CoreV1EventList(
            items=[event()], metadata=client.V1ListMeta())
        self.metrics.get_namespaced_custom_object.return_value = {
            "metadata": {"uid": "pod-uid"}, "timestamp": "2026-10-17T00:00:00Z",
            "window": "15s", "containers": [{"name": "app", "usage": {"cpu": "1m", "memory": "1Mi"}}]}

    def test_all_tools_are_silent_serializable_and_independent(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            information = pod_information.collect_pod_information("aiops", "demo", core_api=self.core)
            self.assertEqual(information["containers"][0]["name"], "app")
            self.core.read_namespaced_pod_log.assert_not_called()
            self.core.list_namespaced_event.assert_not_called()
            logs = container_logs.collect_container_logs("aiops", "demo", "app", core_api=self.core)
            events = kubernetes_events.collect_kubernetes_events("aiops", "demo", pod_uid="pod-uid", core_api=self.core)
            metrics = resource_metrics.collect_resource_metrics("aiops", "demo", pod_uid="pod-uid", metrics_api=self.metrics)
        for result in (information, logs, events, metrics):
            self.assertEqual(result["collection_errors"], [])
            json.dumps(result)
        self.assertNotIn("never-print-me", json.dumps(information))
        self.assertEqual(set(logs), {"logs", "collection_errors"})
        self.assertEqual(len(events["events"]), 1)
        self.assertEqual(metrics["metrics"]["containers"][0]["cpu"], "1m")
        self.core.read_namespaced_pod.assert_called_once()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_each_tool_can_load_its_own_clients(self):
        cases = [
            (pod_information, pod_information.collect_pod_information, ("aiops", "demo"), {}),
            (container_logs, container_logs.collect_container_logs, ("aiops", "demo", "app"), {"tail_lines": 20}),
            (kubernetes_events, kubernetes_events.collect_kubernetes_events, ("aiops", "demo"), {"pod_uid": "pod-uid"}),
            (resource_metrics, resource_metrics.collect_resource_metrics, ("aiops", "demo"), {"pod_uid": "pod-uid"}),
        ]
        for module, function, args, kwargs in cases:
            with self.subTest(tool=function.__name__), patch.object(module, "kubernetes_clients") as factory:
                factory.return_value.__enter__.return_value = (self.core, self.metrics)
                json.dumps(function(*args, **kwargs))
                factory.assert_called_once_with()
                factory.return_value.__exit__.assert_called_once()

    def test_optional_errors_are_returned_locally_without_api_details(self):
        self.core.read_namespaced_pod_log.side_effect = ApiException(status=400, reason="credential-value")
        self.core.list_namespaced_event.side_effect = ApiException(status=403, reason="credential-value")
        self.metrics.get_namespaced_custom_object.side_effect = ApiException(status=404, reason="credential-value")
        logs = container_logs.collect_container_logs("aiops", "demo", "app", core_api=self.core)
        events = kubernetes_events.collect_kubernetes_events("aiops", "demo", core_api=self.core)
        metrics = resource_metrics.collect_resource_metrics("aiops", "demo", metrics_api=self.metrics)
        self.assertEqual(logs["logs"]["app"], {"current": None, "previous": None})
        self.assertEqual(len(logs["collection_errors"]), 2)
        self.assertEqual(events["events"], [])
        self.assertEqual(events["collection_errors"][0]["status"], 403)
        self.assertIsNone(metrics["metrics"])
        self.assertEqual(metrics["collection_errors"][0]["operation"], "metrics")
        self.assertNotIn("credential-value", json.dumps([logs, events, metrics]))
        self.core.read_namespaced_pod.assert_not_called()

    def test_pod_tool_raises_meaningful_error(self):
        self.core.read_namespaced_pod.side_effect = ApiException(status=404)
        with self.assertRaises(PodEvidenceError) as error:
            pod_information.collect_pod_information("aiops", "demo", core_api=self.core)
        self.assertEqual(error.exception.status, 404)
        self.assertIn("aiops/demo", str(error.exception))

    def test_event_name_fallback_filters_unrelated_pods(self):
        unrelated = event()
        unrelated.involved_object.name = "another-pod"
        self.core.list_namespaced_event.return_value.items.append(unrelated)
        result = kubernetes_events.collect_kubernetes_events("aiops", "demo", core_api=self.core)
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("involvedObject.name=demo", self.core.list_namespaced_event.call_args.kwargs["field_selector"])

    def test_metrics_uid_mismatch_is_partial_error(self):
        result = resource_metrics.collect_resource_metrics("aiops", "demo", pod_uid="new-uid", metrics_api=self.metrics)
        self.assertIsNone(result["metrics"])
        self.assertEqual(result["collection_errors"][0]["error_type"], "PodIdentityChanged")

    def test_bad_input_is_rejected_before_clients_are_loaded(self):
        cases = [
            (pod_information, pod_information.collect_pod_information, ("", "demo"), {}),
            (container_logs, container_logs.collect_container_logs, ("aiops", "demo", ""), {}),
            (container_logs, container_logs.collect_container_logs, ("aiops", "demo", "app"), {"tail_lines": 1001}),
            (kubernetes_events, kubernetes_events.collect_kubernetes_events, ("aiops", ""), {}),
            (resource_metrics, resource_metrics.collect_resource_metrics, ("aiops", ""), {}),
        ]
        for module, function, args, kwargs in cases:
            with self.subTest(tool=function.__name__), patch.object(module, "kubernetes_clients") as factory:
                with self.assertRaises(ValueError):
                    function(*args, **kwargs)
                factory.assert_not_called()

    def test_client_configuration_error_has_same_public_exception(self):
        with patch.object(kubernetes_client.config, "load_kube_config", side_effect=ConfigException("private details")):
            with self.assertRaises(PodEvidenceError) as error:
                pod_information.collect_pod_information("aiops", "demo")
        self.assertIn("Cannot load kubeconfig", str(error.exception))
        self.assertNotIn("private details", str(error.exception))
