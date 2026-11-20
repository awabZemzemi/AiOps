"""Ensure evidence packaging does not depend on graph imports or sys.path edits."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageImportTests(unittest.TestCase):
    def run_python(self, code):
        subprocess.run([sys.executable, '-B', '-c', code], cwd=ROOT, check=True,
                       capture_output=True, text=True)

    def test_collectors_import_without_http_or_agent_dependencies(self):
        self.run_python('''
import sys
from diagnostic_service.diagnostics import collect_pod_evidence
from diagnostic_service.tools import container_logs, pod_listing
from diagnostic_service import kubernetes_client
assert 'backend' not in sys.modules
assert 'agent' not in sys.modules
assert 'fastapi' not in sys.modules
''')

    def test_router_and_app_import_before_graph_without_path_mutation(self):
        self.run_python('''
import sys
original = list(sys.path)
from backend.evidence.routes import router
from backend.main import app
from agent.tools import get_tools
assert sys.path == original
assert len(get_tools()) == 5
''')

    def test_cli_module_help_needs_no_runtime_credentials(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'diagnostic_service', '--help'],
                                cwd=ROOT, check=True, capture_output=True, text=True)
        self.assertIn('Collect read-only Kubernetes Pod evidence', result.stdout)
