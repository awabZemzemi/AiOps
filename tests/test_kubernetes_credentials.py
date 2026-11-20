"""Offline identity selection, credential workflow, and exact RBAC contract checks."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import yaml
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from diagnostic_service import kubernetes_client as factory
from diagnostic_service.evidence_common import PodEvidenceError
from scripts.create_agent_kubeconfig import create
from diagnostic_service.tools.pod_information import collect_pod_information

ROOT = Path(__file__).resolve().parents[1]


class CredentialConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.path = self.directory / 'agent.json'
        self.path.write_text('{}')
        self.env = self.enterContext(patch.dict(os.environ, {
            'AIOPS_KUBERNETES_MODE': 'kubeconfig', 'AIOPS_KUBECONFIG_PATH': str(self.path),
            'KUBECONFIG': '/never-use-developer-config',
        }))
        self.kube = self.enterContext(patch.object(factory.config, 'load_kube_config', side_effect=self.configure))
        self.incluster = self.enterContext(patch.object(factory.config, 'load_incluster_config', side_effect=self.configure))
        self.api = self.enterContext(patch.object(factory.client, 'ApiClient'))
        self.api.return_value.__enter__.return_value = Mock()

    def configure(self, **kwargs):
        configuration = kwargs['client_configuration']
        configuration.host = 'https://127.0.0.1:6443'
        configuration.verify_ssl = True
        configuration.ssl_ca_cert = str(self.path)

    def test_local_explicit_file_and_private_client_lifecycle(self):
        with factory.kubernetes_clients() as (core, metrics):
            self.assertIsInstance(core, client.CoreV1Api)
            self.assertIsInstance(metrics, client.CustomObjectsApi)
        self.kube.assert_called_once()
        self.assertEqual(self.kube.call_args.kwargs['config_file'], str(self.path))
        self.assertFalse(self.kube.call_args.kwargs['persist_config'])
        self.assertEqual(self.api.call_args.args[0].retries, 0)
        self.api.return_value.__exit__.assert_called_once()
        self.incluster.assert_not_called()

    def test_incluster_only_uses_projected_serviceaccount(self):
        os.environ['AIOPS_KUBERNETES_MODE'] = 'incluster'
        with factory.kubernetes_clients():
            pass
        self.incluster.assert_called_once()
        self.kube.assert_not_called()

    def test_missing_or_unknown_modes_never_fall_back(self):
        for mode in ['', 'auto', 'developer']:
            os.environ['AIOPS_KUBERNETES_MODE'] = mode
            with self.subTest(mode=mode), self.assertRaises(PodEvidenceError):
                with factory.kubernetes_clients():
                    pass
        self.kube.assert_not_called()
        self.incluster.assert_not_called()
        self.api.assert_not_called()

    def test_missing_file_and_path_fail_without_client_creation(self):
        for path in ['', str(self.directory / 'missing')]:
            os.environ['AIOPS_KUBECONFIG_PATH'] = path
            with self.assertRaises(PodEvidenceError):
                with factory.kubernetes_clients():
                    pass
        self.kube.assert_not_called()
        self.api.assert_not_called()

    def test_loader_errors_are_safe_and_never_fall_back(self):
        for mode, loader in [('kubeconfig', self.kube), ('incluster', self.incluster)]:
            os.environ['AIOPS_KUBERNETES_MODE'] = mode
            for error in [ConfigException('private-token-value'), OSError('private-token-value'), ValueError('private-token-value')]:
                loader.side_effect = error
                with self.assertRaises(PodEvidenceError) as caught:
                    with factory.kubernetes_clients():
                        pass
                self.assertNotIn('private-token-value', str(caught.exception))
                self.assertNotIn('private-token-value', str(caught.exception.__cause__))
        self.api.assert_not_called()

    def test_unverified_tls_is_rejected(self):
        for field, value in [('verify_ssl', False), ('host', 'http://127.0.0.1'), ('ssl_ca_cert', '')]:
            def configure(**kwargs):
                self.configure(**kwargs)
                setattr(kwargs['client_configuration'], field, value)
            self.kube.side_effect = configure
            with self.subTest(field=field), self.assertRaises(PodEvidenceError):
                with factory.kubernetes_clients():
                    pass
        self.api.assert_not_called()

    def test_expired_token_401_is_reported_without_fallback(self):
        core = Mock()
        core.read_namespaced_pod.side_effect = ApiException(status=401, reason='private-token-value')
        with self.assertRaises(PodEvidenceError) as caught:
            collect_pod_information('aiops', 'demo', core_api=core)
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn('private-token-value', str(caught.exception))
        self.kube.assert_not_called()


class CredentialWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.output = self.directory / 'private/agent.yaml'
        self.source = {'clusters': [{'cluster': {'server': 'https://127.0.0.1:6443', 'certificate-authority-data': 'Y2E='}}],
                       'users': [{'user': {'client-key-data': 'developer-key-never-copy'}}]}
        self.issued = {'status': {'token': 'synthetic-test-token', 'expirationTimestamp': '2030-01-01T00:00:00Z'}}
        self.run = self.enterContext(patch('scripts.create_agent_kubeconfig.kubectl_json', side_effect=[self.source, self.issued]))

    def test_private_kubeconfig_copies_only_ca_server_and_serviceaccount_token(self):
        create('kind-test', self.output)
        document = json.loads(self.output.read_text())
        self.assertNotIn('developer-key-never-copy', self.output.read_text())
        self.assertEqual(document['users'][0]['user'], {'token': 'synthetic-test-token'})
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.output.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn('token', self.run.call_args.args)

    def test_personal_config_and_symlink_outputs_are_rejected(self):
        with self.assertRaises(ValueError):
            create('kind-test', ROOT / 'agent.kubeconfig')
        with self.assertRaises(ValueError):
            create('kind-test', Path.home() / '.kube/config')
        target = self.directory / 'target'
        target.write_text('preserve')
        link = self.directory / 'link'
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            create('kind-test', link)
        self.assertEqual(target.read_text(), 'preserve')
        self.run.assert_not_called()

    def test_insecure_source_tls_is_rejected_before_token_request(self):
        self.source['clusters'][0]['cluster']['insecure-skip-tls-verify'] = True
        with self.assertRaises(ValueError):
            create('kind-test', self.output)
        self.assertEqual(self.run.call_count, 1)
        self.assertFalse(self.output.exists())

    def test_exact_rbac_permissions(self):
        sa, role, binding = list(yaml.safe_load_all((ROOT / 'deploy/aiops/manifests/aiops-agent-rbac.yaml').read_text()))
        self.assertEqual([sa['kind'], role['kind'], binding['kind']], ['ServiceAccount', 'Role', 'RoleBinding'])
        actual = {(g, r, v) for rule in role['rules'] for g in rule['apiGroups'] for r in rule['resources'] for v in rule['verbs']}
        self.assertEqual(actual, {('', 'pods', 'get'), ('', 'pods', 'list'), ('', 'pods/log', 'get'),
                                  ('', 'events', 'list'), ('metrics.k8s.io', 'pods', 'get')})
        self.assertTrue(all(obj['metadata']['namespace'] == 'aiops' for obj in [sa, role, binding]))
        self.assertEqual(binding['subjects'], [{'kind':'ServiceAccount','name':'aiops-agent','namespace':'aiops'}])
        self.assertEqual(binding['roleRef']['name'], role['metadata']['name'])
