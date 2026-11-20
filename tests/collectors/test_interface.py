import contextlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from diagnostic_service import kubernetes_client
from diagnostic_service import main
from diagnostic_service.diagnostics import PodEvidenceError


class InterfaceTests(unittest.TestCase):
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
