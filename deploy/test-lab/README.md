# Kubernetes Test Lab

This deployment unit owns only SentinelShop, Redis, crash-demo, and lab-specific
configuration. It has no AIOps authentication, diagnostic ServiceAccount or RBAC.
The existing service code and Dockerfiles remain under `app/`; Docker Compose
remains `app/docker-compose.yml`. Its manual integration runner is
`tests/test_lab_integration.py`. All commands below run from the repository root.

Create a new Kind cluster using [infra/kind](../../infra/kind/README.md), or keep
the existing `sentinelops` cluster. Build/load the five app images using the
[chart guide](helm/sentinelshop/README.md), then install an independent release:

```bash
helm upgrade --install sentinelshop-lab deploy/test-lab/helm/sentinelshop \
  --kube-context kind-sentinelops -n aiops --create-namespace
kubectl --context kind-sentinelops -n aiops get pods,svc,pvc \
  -l app.kubernetes.io/instance=sentinelshop-lab
kubectl --context kind-sentinelops -n aiops \
  port-forward service/sentinelshop-lab-frontend 8080:80
```

`sentinelshop-lab-crash-demo` uses the same busybox command, three-second delay,
error log and exit code as the existing raw `crash-demo`. Repeated crashes are
intentional. Disable it with `--set crashDemo.enabled=false` when testing only
healthy services. Do not use Helm `--wait` as a health assertion for this chart;
check the six healthy Deployments/StatefulSet separately and expect crash-demo
restarts. Existing failure-injection exercises are preserved in the
[raw manifest guide](manifests/README.md).

The raw manifests are an alternative deployment, not a second manager for Helm
objects. Do not apply them over Helm resources. They retain existing names for
local compatibility; Helm uses release-prefixed names. The new release does not
adopt the currently running raw workloads, the raw crash-demo, or their PVCs.
Installing alongside them temporarily duplicates lab services and Redis data.
Plan any live migration separately; no existing resources are deleted or adopted
by this repository reorganization.

```bash
helm lint deploy/test-lab/helm/sentinelshop
helm template sentinelshop-lab deploy/test-lab/helm/sentinelshop -n aiops
uv run --locked python -m unittest tests.test_lab_chart -v
helm uninstall sentinelshop-lab -n aiops --kube-context kind-sentinelops
```

Uninstall removes this release's workloads, including its crash Pod; the Redis
PVC is retained. Keep namespace `aiops`: deleting it destroys raw workloads/data
and diagnostic RoleBindings owned by an AIOps release. Uninstalling this Helm
release does not uninstall AIOps in `aiops-system`, whose chart has no dependency
on the Test Lab. The diagnostic target namespace can also contain unrelated
workloads; AIOps does not require installing SentinelShop.
