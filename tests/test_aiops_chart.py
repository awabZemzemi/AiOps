"""Offline deployment contract tests for the standalone AIOps chart."""
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(*options):
    result = subprocess.check_output(['helm', 'template', 'aiops', str(ROOT / 'deploy/aiops/helm/aiops'),
                                      '-n', 'aiops-system', *options], text=True)
    return [doc for doc in yaml.safe_load_all(result) if doc]


class AIOpsChartTests(unittest.TestCase):
    def test_namespace_identity_and_exact_read_only_rbac(self):
        docs = render()
        self.assertEqual({d['kind'] for d in docs}, {'ServiceAccount','Role','RoleBinding',
                                                   'Deployment','Service','PersistentVolumeClaim'})
        role = next(d for d in docs if d['kind'] == 'Role')
        standalone = list(yaml.safe_load_all((ROOT / 'deploy/aiops/manifests/aiops-agent-rbac.yaml').read_text()))[1]
        self.assertEqual(role['rules'], standalone['rules'])
        binding = next(d for d in docs if d['kind'] == 'RoleBinding')
        self.assertEqual(role['metadata']['namespace'], 'aiops')
        self.assertEqual(binding['metadata']['namespace'], 'aiops')
        self.assertEqual(binding['roleRef']['name'], role['metadata']['name'])
        self.assertEqual(binding['subjects'], [{'kind':'ServiceAccount','name':'aiops-agent','namespace':'aiops-system'}])
        for doc in docs:
            if doc['kind'] not in ['Role', 'RoleBinding']:
                self.assertEqual(doc['metadata']['namespace'], 'aiops-system')

    def test_startup_migrations_storage_and_secrets(self):
        docs = render()
        workload = next(d for d in docs if d['kind'] == 'Deployment')
        spec = workload['spec']
        self.assertEqual(spec['replicas'], 1)
        self.assertEqual(spec['strategy']['type'], 'Recreate')
        pod = spec['template']['spec']
        self.assertEqual(pod['serviceAccountName'], 'aiops-agent')
        self.assertTrue(pod['automountServiceAccountToken'])
        self.assertEqual(pod['securityContext']['runAsUser'], 10001)
        self.assertTrue(pod['securityContext']['runAsNonRoot'])
        init = pod['initContainers'][0]
        container = pod['containers'][0]
        self.assertEqual(init['command'], ['alembic','upgrade','head'])
        self.assertEqual(init['image'], container['image'])
        env = {v['name']:v for v in container['env']}
        self.assertEqual(env['AIOPS_KUBERNETES_MODE']['value'], 'incluster')
        self.assertEqual(env['AIOPS_DATABASE_URL']['value'], 'sqlite:////data/aiops.db')
        self.assertEqual(env['AIOPS_LOG_DIR']['value'], '/logs/backend')
        for key in ['AIOPS_JWT_SECRET','GROQ_API_KEY']:
            self.assertEqual(env[key]['valueFrom']['secretKeyRef'], {'name':'aiops-backend-secrets','key':key})
        for c in [init,container]:
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
            self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
            self.assertEqual({v['mountPath'] for v in c['volumeMounts']}, {'/data','/logs','/tmp'})
        pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
        self.assertEqual(pvc['metadata']['annotations']['helm.sh/resource-policy'], 'keep')
        self.assertNotIn('storageClassName', pvc['spec'])
        volume = next(v for v in pod['volumes'] if v['name'] == 'data')
        self.assertEqual(volume['persistentVolumeClaim']['claimName'], pvc['metadata']['name'])
        service = next(d for d in docs if d['kind'] == 'Service')
        self.assertEqual(service['spec']['selector'], spec['selector']['matchLabels'])
        self.assertEqual(service['spec']['type'], 'ClusterIP')
        self.assertEqual(container['readinessProbe']['httpGet']['path'], '/health')

    def test_overrides_and_externally_managed_rbac(self):
        docs = render('--set','rbac.create=false','--set','image.tag=v2','--set','existingSecret=private-config',
                      '--set','serviceAccount.name=custom-agent','--set','persistence.storageClass=fast')
        self.assertFalse(any(d['kind'] in ['Role','RoleBinding'] for d in docs))
        pod = next(d for d in docs if d['kind'] == 'Deployment')['spec']['template']['spec']
        self.assertEqual(pod['serviceAccountName'], 'custom-agent')
        self.assertEqual(pod['containers'][0]['image'], 'aiops-backend:v2')
        pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
        self.assertEqual(pvc['spec']['storageClassName'], 'fast')
        secret_refs = [v['valueFrom']['secretKeyRef']['name'] for v in pod['containers'][0]['env'] if 'valueFrom' in v]
        self.assertEqual(secret_refs, ['private-config','private-config'])

    def test_default_release_does_not_adopt_standalone_rbac_objects(self):
        standalone = list(yaml.safe_load_all((ROOT / 'deploy/aiops/manifests/aiops-agent-rbac.yaml').read_text()))
        identity = lambda doc: (doc['kind'], doc['metadata']['namespace'], doc['metadata']['name'])
        self.assertFalse({identity(d) for d in standalone} & {identity(d) for d in render()})

    def test_custom_namespace_binding_stays_namespaced(self):
        docs = render('--set','rbac.targetNamespace=diagnostic-target','--set','serviceAccount.name=custom-agent')
        role = next(d for d in docs if d['kind'] == 'Role')
        binding = next(d for d in docs if d['kind'] == 'RoleBinding')
        self.assertEqual(role['metadata']['namespace'], 'diagnostic-target')
        self.assertEqual(binding['metadata']['namespace'], 'diagnostic-target')
        self.assertEqual(binding['subjects'], [{'kind':'ServiceAccount','name':'custom-agent','namespace':'aiops-system'}])
        self.assertFalse(any(d['kind'] in ['ClusterRole','ClusterRoleBinding'] for d in docs))
