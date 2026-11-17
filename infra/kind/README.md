# Local Kind infrastructure

From the repository root, create a new cluster only if it does not already exist:

```bash
kind get clusters
kind create cluster --name sentinelops --config infra/kind/cluster.yaml
kubectl --context kind-sentinelops get nodes
```

The single control-plane configuration needs no host port mappings: use
`kubectl port-forward` for the test app and diagnostic API. Kind local-path PVCs
are local development storage and are lost if the cluster is deleted. This
configuration is optional; existing `sentinelops` clusters need no recreation.

Deploy [the Test Lab](../../deploy/test-lab/README.md) and
[AIOps](../../deploy/aiops/README.md) independently. Nothing in these instructions
installs a cluster-wide role, changes personal kubeconfig selection, or sends
workload evidence to a model. `kind create cluster` adds its normal context to
kubectl configuration; the local backend still uses its separate restricted file.
