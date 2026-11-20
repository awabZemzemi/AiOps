"""Opt-in read-only integration test using explicit diagnostic credentials."""
import json
import os
from pathlib import Path
import sys
import unittest

from diagnostic_service.diagnostics import collect_pod_evidence


@unittest.skipUnless(os.environ.get("RUN_K8S_INTEGRATION") == "1", "Set RUN_K8S_INTEGRATION=1 to query the cluster")
class IntegrationTest(unittest.TestCase):
    def test_crash_demo(self):
        report = collect_pod_evidence("aiops", "crash-demo")
        self.assertEqual(report["pod"]["name"], "crash-demo")
        self.assertEqual(report["target"]["namespace"], "aiops")
        self.assertIn("crashing-container", report["logs"])
        self.assertTrue(report["containers"])
        json.dumps(report)
