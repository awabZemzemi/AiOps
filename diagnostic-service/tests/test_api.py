"""HTTP boundary tests: no kubeconfig or live Kubernetes API is needed."""
import asyncio
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from kubernetes.client.exceptions import ApiException

import api
from diagnostics import DEFAULT_LOG_LINES, MAX_LOG_LINES, PodEvidenceError

REPORT = {
    "timestamp": "2026-10-20T10:00:00+00:00",
    "target": {"namespace": "aiops", "pod": "crash-demo"},
    "pod": {"name": "crash-demo", "namespace": "aiops", "phase": "Running"},
    "containers": [{"name": "app", "state": {"state": "Running"}}],
    "logs": {"app": {"current": "2026-10-20T10:00:00Z started\n", "previous": None}},
    "events": [], "metrics": {"timestamp": "2026-10-20T10:00:00Z", "window": "15s", "containers": []},
    "collection_errors": [],
}
URL = "/api/v1/pods/aiops/crash-demo/evidence"


class ApiTests(unittest.TestCase):
    def setUp(self):
        # Production-style error responses, rather than re-raising server exceptions in tests.
        self.client = TestClient(api.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        patcher = patch("api.collect_pod_evidence", return_value=deepcopy(REPORT))
        self.collector = patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_does_not_contact_kubernetes(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.collector.assert_not_called()

    def test_success_preserves_entire_report_and_default(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), REPORT)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.collector.assert_called_once_with("aiops", "crash-demo", tail_lines=DEFAULT_LOG_LINES)

    def test_log_limit_override_and_bounds(self):
        for limit in (1, 50, MAX_LOG_LINES):
            with self.subTest(limit=limit):
                response = self.client.get(URL, params={"tail_lines": limit})
                self.assertEqual(response.status_code, 200)
                self.collector.assert_called_with("aiops", "crash-demo", tail_lines=limit)

    def test_invalid_log_limit_does_not_call_collector(self):
        for limit in (0, -1, MAX_LOG_LINES + 1, "abc", "1.5", "true", ""):
            with self.subTest(limit=limit):
                response = self.client.get(URL, params={"tail_lines": limit})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"][0]["loc"], ["query", "tail_lines"])
                self.collector.assert_not_called()

    def test_invalid_path_parameters_do_not_call_collector(self):
        cases = [("BadNamespace", "demo"), ("has.dot", "demo"), ("a" * 64, "demo"),
                 ("aiops", "BadPod"), ("aiops", "bad_pod"), ("aiops", "-demo"),
                 ("aiops", "demo."), ("aiops", "demo..pod"), ("aiops", "a" * 254),
                 ("%20", "demo")]
        for namespace, pod in cases:
            with self.subTest(namespace=namespace, pod=pod):
                response = self.client.get(f"/api/v1/pods/{namespace}/{pod}/evidence")
                self.assertEqual(response.status_code, 422)
                self.collector.assert_not_called()

    def test_valid_dns_names_are_forwarded(self):
        namespace, pod = "a" * 63, "demo.pod-1"
        response = self.client.get(f"/api/v1/pods/{namespace}/{pod}/evidence")
        self.assertEqual(response.status_code, 200)
        self.collector.assert_called_once_with(namespace, pod, tail_lines=DEFAULT_LOG_LINES)

    def test_nonexistent_pod_returns_safe_404(self):
        self.collector.side_effect = PodEvidenceError("private API response and credentials", status=404)
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Pod not found."})
        self.assertNotIn("private", response.text)

    def test_kubernetes_error_statuses_are_safe(self):
        for upstream, expected in ((403, 403), (429, 429), (400, 502), (401, 503),
                                   (500, 503), (503, 503), (0, 503), (None, 503)):
            with self.subTest(upstream=upstream):
                self.collector.side_effect = PodEvidenceError("Bearer secret-value", status=upstream)
                response = self.client.get(URL)
                self.assertEqual(response.status_code, expected)
                self.assertNotIn("secret-value", response.text)
                self.assertNotIn("Bearer", response.text)

    def test_unavailable_metrics_keeps_partial_report_successful(self):
        report = deepcopy(REPORT)
        report["metrics"] = None
        report["collection_errors"] = [{"operation": "metrics", "status": 404,
                                        "error_type": "ApiException", "message": "Resource unavailable"}]
        self.collector.return_value = report
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), report)

    def test_unexpected_errors_never_expose_exception_details(self):
        for error in (RuntimeError("private token"), ApiException(status=500, reason="private token")):
            with self.subTest(error=type(error).__name__):
                self.collector.side_effect = error
                response = self.client.get(URL)
                self.assertEqual(response.status_code, 500)
                self.assertEqual(response.json(), {"detail": "Evidence collection failed."})
                self.assertNotIn("private token", response.text)

    def test_blocking_collector_runs_outside_the_event_loop(self):
        def blocking_collection(*args, **kwargs):
            with self.assertRaises(RuntimeError):
                asyncio.get_running_loop()
            return deepcopy(REPORT)
        self.collector.side_effect = blocking_collection
        self.assertEqual(self.client.get(URL).status_code, 200)

    def test_swagger_and_openapi_describe_the_contract(self):
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(set(schema["paths"]), {"/health", "/api/v1/pods/{namespace}/{pod_name}/evidence"})
        operation = schema["paths"]["/api/v1/pods/{namespace}/{pod_name}/evidence"]["get"]
        self.assertNotIn("requestBody", operation)
        query = next(p for p in operation["parameters"] if p["name"] == "tail_lines")
        self.assertEqual(query["schema"]["default"], DEFAULT_LOG_LINES)
        self.assertEqual(query["schema"]["maximum"], MAX_LOG_LINES)
        self.assertEqual(query["schema"]["minimum"], 1)
        self.collector.assert_not_called()

    def test_success_logging_omits_evidence_contents(self):
        self.collector.return_value["logs"]["app"]["current"] = "secret container log payload"
        with self.assertLogs(api.logger, level="INFO") as captured:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        output = "\n".join(captured.output)
        self.assertIn("evidence_started namespace=aiops pod=crash-demo", output)
        self.assertIn("evidence_completed", output)
        self.assertIn("duration_ms=", output)
        self.assertNotIn("secret container log payload", output)
        self.assertTrue(all(record.exc_info is None for record in captured.records))

    def test_expected_failure_logging_is_safe(self):
        self.collector.side_effect = PodEvidenceError("Bearer private-kubeconfig-token", status=403)
        with self.assertLogs(api.logger, level="INFO") as captured:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 403)
        output = "\n".join(captured.output)
        self.assertIn("WARNING", output)
        self.assertIn("evidence_failed namespace=aiops pod=crash-demo http_status=403", output)
        self.assertNotIn("private-kubeconfig-token", output)
        self.assertNotIn("evidence_completed", output)

    def test_partial_failure_logging_omits_error_messages(self):
        self.collector.return_value["metrics"] = None
        self.collector.return_value["collection_errors"] = [
            {"operation": "metrics", "status": 404, "message": "sensitive upstream response"}]
        with self.assertLogs(api.logger, level="INFO") as captured:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        output = "\n".join(captured.output)
        self.assertIn("evidence_partial namespace=aiops pod=crash-demo error_count=1", output)
        self.assertIn("evidence_completed", output)
        self.assertNotIn("sensitive upstream response", output)

    def test_unexpected_failure_does_not_escape_to_server_logging(self):
        self.collector.side_effect = RuntimeError("private-token-in-exception")
        # With re-raising enabled, a global Exception handler would leak the
        # original error back to the server. The route must handle it instead.
        with TestClient(api.app, raise_server_exceptions=True) as client:
            with self.assertLogs(api.logger, level="INFO") as captured:
                response = client.get(URL)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "Evidence collection failed."})
        output = "\n".join(captured.output)
        self.assertIn("ERROR", output)
        self.assertIn("evidence_unexpected_error", output)
        self.assertNotIn("private-token-in-exception", output)
        self.assertTrue(all(record.exc_info is None for record in captured.records))


if __name__ == "__main__":
    unittest.main()
