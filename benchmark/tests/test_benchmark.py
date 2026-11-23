"""Offline contract and safety tests. Every Kubernetes interaction is mocked."""
import copy
import json
import unittest
from unittest.mock import patch

from benchmark.catalog import load_catalog, missing_tools, public_case, validate
from benchmark.evaluation import make_result, validate_result
from benchmark.manifests import build, validate_manifest
from benchmark.runtime import activate, cleanup, create, owned_resources
from benchmark.safety import SafetyError, check_budget, check_namespace_idle, cluster_identity, footprint
from benchmark.workload import Handler


CASES = load_catalog()


class CatalogTests(unittest.TestCase):
    def test_all_thirty_have_explicit_ground_truth_and_implementation_state(self):
        self.assertEqual(len(CASES), 30)
        for case in CASES.values():
            validate(case)
            self.assertTrue(case["ground_truth"])
            self.assertTrue(case["evidence"])
            self.assertTrue(case["verify"])
            self.assertNotEqual(case["status"], "validated")

    def test_truth_separated_from_agent_view(self):
        public = public_case(CASES["INC-001"])
        self.assertNotIn("ground_truth", json.dumps(public))
        self.assertNotIn("exit 42", json.dumps(public))

    def test_missing_tool_detection(self):
        self.assertEqual(missing_tools(CASES["INC-001"], {"list_pods"}), ["get_events", "get_logs", "get_pod_info"])

    def test_unsupported_case_cannot_build(self):
        with self.assertRaises(ValueError):
            build(CASES["INC-030"])


class SafetyTests(unittest.TestCase):
    def test_all_live_manifests_fit_budget_and_have_no_host_access(self):
        for case in CASES.values():
            if case["mode"] == "live-light":
                docs = build(case)
                self.assertEqual(check_budget(docs, case["id"]), footprint(docs))
                for doc in docs:
                    validate_manifest(doc, case["id"])

    def test_budget_rejects_large_pod_count(self):
        case = CASES["INC-001"]
        docs = build(case)
        docs.extend(copy.deepcopy(docs[-1]) for _ in range(8))
        with self.assertRaises(SafetyError):
            check_budget(docs, case["id"])

    def test_unsafe_manifest_rejection(self):
        case = CASES["INC-001"]
        pod = build(case)[-1]
        pod["spec"]["volumes"].append({"name": "host", "hostPath": {"path": "/"}})
        with self.assertRaises(ValueError):
            validate_manifest(pod, case["id"])
        pod["spec"]["volumes"].pop()
        pod["spec"]["containers"][0]["securityContext"]["privileged"] = True
        with self.assertRaises(ValueError):
            validate_manifest(pod, case["id"])

    def test_image_pulls_and_pod_lifetimes_are_bounded(self):
        for case in CASES.values():
            if case["mode"] != "live-light":
                continue
            for doc in build(case):
                if doc["kind"] != "Pod":
                    continue
                self.assertEqual(doc["spec"]["activeDeadlineSeconds"], 900)
                container = doc["spec"]["containers"][0]
                if case["id"] == "INC-002":
                    self.assertEqual(container["imagePullPolicy"], "Always")
                    self.assertTrue(container["image"].startswith("127.0.0.1:1/"))
                else:
                    self.assertEqual(container["imagePullPolicy"], "Never")

    @patch("benchmark.safety.run")
    def test_context_rejected(self, run):
        run.return_value = "production\n"
        with self.assertRaisesRegex(SafetyError, "Active kubectl context"):
            cluster_identity()

    @patch("benchmark.safety.kube_json")
    @patch("benchmark.safety.run")
    def test_remote_cluster_rejected(self, run, kube_json):
        run.side_effect = ["kind-sentinelops", "sentinelops"]
        kube_json.return_value = {"clusters": [{"cluster": {"server": "https://prod.example:6443"}}]}
        with self.assertRaisesRegex(SafetyError, "loopback"):
            cluster_identity()

    @patch("benchmark.safety.kube_json")
    @patch("benchmark.safety.namespace_state")
    def test_concurrent_scenario_rejected(self, ns, kube_json):
        ns.return_value = {"metadata": {"labels": {"app.kubernetes.io/managed-by": "aiops-benchmark"}}}
        kube_json.return_value = {"items": [{"metadata": {"name": "active-incident"}}]}
        with self.assertRaisesRegex(SafetyError, "Another incident"):
            check_namespace_idle()

    @patch("benchmark.runtime.kube")
    def test_server_dry_run_flag(self, kube):
        create(build(CASES["INC-001"])[0], dry_run=True)
        self.assertIn("--dry-run=server", kube.call_args.args)

    @patch("benchmark.runtime.create")
    @patch("benchmark.runtime.cleanup")
    @patch("benchmark.runtime.check_namespace_idle")
    @patch("benchmark.runtime.ensure_namespace")
    @patch("benchmark.runtime.preflight")
    def test_partial_setup_rolls_back(self, preflight, ensure, idle, cleanup_mock, create_mock):
        preflight.return_value = {}
        # Two dry-run creates, then marker, then workload creation fails.
        create_mock.side_effect = [None, None, None, RuntimeError("API failure")]
        with self.assertRaisesRegex(SafetyError, "rolled back"):
            activate(CASES["INC-001"])
        self.assertEqual(create_mock.call_count, 4)
        cleanup_mock.assert_called_once()

    @patch("benchmark.runtime.kube_json")
    def test_ownership_prevents_foreign_cleanup(self, kube_json):
        kube_json.return_value = {"metadata": {"uid": "foreign", "labels": {}}}
        with self.assertRaisesRegex(SafetyError, "unowned"):
            owned_resources(CASES["INC-001"], "run-1")

    @patch("benchmark.runtime.cluster_identity")
    @patch("benchmark.runtime.active")
    def test_cleanup_rejects_wrong_incident(self, active, identity):
        active.return_value = {"incident_id": "INC-002", "run_id": "run-2"}
        with self.assertRaisesRegex(SafetyError, "not active"):
            cleanup(CASES["INC-001"])


class EvaluationTests(unittest.TestCase):
    def test_result_schema_and_unknown_metrics(self):
        result = make_result(CASES["INC-001"], agent_version="model-v1", agent_text="Observed restart",
                             scores={"root_cause": 2, "evidence": 2, "recovery": 1, "uncertainty": 1},
                             environment="local", test_type="live", reproduced=True,
                             reviewed=True, safety_pass=True)
        self.assertEqual(result["total"], 6)
        self.assertIsNone(result["duration_seconds"])
        validate_result(result)
        result["scores"]["root_cause"] = 5
        with self.assertRaises(ValueError):
            validate_result(result)


class WorkloadTests(unittest.TestCase):
    def test_functional_endpoint_reports_real_missing_file(self):
        handler = Handler.__new__(Handler)
        handler.path = "/functional"
        replies = []
        handler.reply = lambda code, payload: replies.append((code, payload))
        with patch.dict("os.environ", {"TEMPLATE_FILE": "/tmp/aiops-benchmark/no-template"}, clear=True):
            handler.do_GET()
        self.assertEqual(replies[0][0], 500)
        self.assertEqual(replies[0][1]["error_type"], "FileNotFoundError")

    def test_health_is_independent_of_functional_failure(self):
        handler = Handler.__new__(Handler)
        handler.path = "/health"
        replies = []
        handler.reply = lambda code, payload: replies.append((code, payload))
        handler.do_GET()
        self.assertEqual(replies, [(200, {"ok": True})])


if __name__ == "__main__":
    unittest.main()
