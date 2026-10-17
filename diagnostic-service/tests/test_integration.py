"""Opt-in read-only integration test against the current kubeconfig context."""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagnostics import collect_pod_evidence


@unittest.skipUnless(os.environ.get("RUN_K8S_INTEGRATION") == "1", "Set RUN_K8S_INTEGRATION=1 to query the cluster")
class IntegrationTest(unittest.TestCase):
    def test_crash_demo(self):
        report = collect_pod_evidence("aiops", "crash-demo")
        self.assertEqual(report["pod"]["name"], "crash-demo")
        self.assertEqual(report["target"]["namespace"], "aiops")
        self.assertIn("crashing-container", report["logs"])
        self.assertTrue(report["containers"])
        json.dumps(report)
