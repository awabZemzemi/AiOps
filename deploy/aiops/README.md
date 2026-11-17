# AIOps deployment and local credentials

Two supported modes are independent of the Test Lab:

- **Local Ubuntu:** run `backend.main:app` using the explicitly selected private
  restricted kubeconfig; follow the setup and renewal instructions below.
- **Kubernetes:** build `backend/Dockerfile` and install
  [`deploy/aiops/helm/aiops`](helm/aiops/README.md) into `aiops-system`. Its Pod uses
  `AIOPS_KUBERNETES_MODE=incluster` with a projected ServiceAccount token, never
  a personal kubeconfig. The chart references an existing Secret and creates no
  default users. Its diagnostic target namespace is independently configurable.

For a new cluster, see [Kind setup](../../infra/kind/README.md). Existing Kind
clusters need no recreation. The optional [Test Lab](../test-lab/README.md) is
not a chart dependency. Its uninstall must leave the target namespace intact.

## Safe RBAC ownership transition

Read-only inspection of `kind-sentinelops` found these existing objects in `aiops`
without Helm ownership labels or release annotations:

- ServiceAccount `aiops-agent`
- Role `aiops-diagnostics-reader`
- RoleBinding `aiops-diagnostics-binding`, binding `aiops/aiops-agent` to that Role

They remain managed by the standalone manifest in `manifests/`. This file move
changes no live object. Do not use `helm --take-ownership`, relabel these objects,
or delete/replace them as part of installing the new release.

With release `aiops` in `aiops-system`, Helm creates a distinct ServiceAccount
`aiops-system/aiops-agent` and target-namespace Role/RoleBinding
`aiops/aiops-backend-reader`. These names do not collide with the inspected local
objects. Keep the default separate release namespace. If deploying to the same
namespace as the standalone account, choose a different `serviceAccount.name`
and inspect all rendered names before installation.

A future live migration should: render and compare object identities; provision
the new release and private Secret; verify its projected identity and allow/deny
matrix; then retire the old local identity only after explicit approval and
confirmation that no local backend still uses it. No adoption, retirement or
live installation was performed during this repository reorganization.

Uninstall only the release:

```bash
helm uninstall aiops -n aiops-system --kube-context kind-sentinelops
```

Its external Secret and retained database PVC remain. The Test Lab is unaffected.
See the [chart guide](helm/aiops/README.md) for image builds, Secret creation,
installation, bootstrap, probes and storage limits.

## Exact permissions

| Collector/tool | Python Kubernetes API method | API group | Resource | Verb |
| --- | --- | --- | --- | --- |
| list_pods | list_namespaced_pod | core (`""`) | pods | list |
| get_pod_info | read_namespaced_pod | core (`""`) | pods | get |
| get_logs | read_namespaced_pod_log | core (`""`) | pods/log | get |
| get_events | list_namespaced_event | core (`""`) | events | list |
| get_metrics | get_namespaced_custom_object | metrics.k8s.io | pods | get |

The direct evidence orchestrator uses the same collectors and factory.
There is no watch, write, Secrets, node, Deployment, cluster-scoped, metrics-list,
or `events.k8s.io` permission. The manifest is separate from the SentinelShop
Helm chart because that chart deploys lab workloads, not the external backend.

## Apply to Kind

Use your administrative context only for provisioning:

```bash
kubectl --context kind-sentinelops apply -f deploy/test-lab/manifests/namespace.yaml
kubectl --context kind-sentinelops apply -f deploy/aiops/manifests/aiops-agent-rbac.yaml
```

The manifest creates `aiops-agent`, `aiops-diagnostics-reader`, and
`aiops-diagnostics-binding`, all in `aiops`. RBAC grants are additive: inspect
existing bindings and repeat verification if the identity already exists.
No built-in roles are modified. No new workload or namespace expansion is made.

## Generate and renew private local credentials

```bash
uv run --locked python scripts/create_agent_kubeconfig.py \
  --context kind-sentinelops \
  --output "$HOME/.config/aiops/agent.kubeconfig" --duration 1h
export AIOPS_KUBERNETES_MODE=kubeconfig
export AIOPS_KUBECONFIG_PATH="$HOME/.config/aiops/agent.kubeconfig"
```

The script uses `kubectl create token aiops-agent` (TokenRequest), captures its
output privately, copies only the server endpoint/CA/TLS server name from the
selected Kind context, and writes an atomic mode-0600 kubeconfig. New output
directories are mode 0700. Developer client keys and credential plugins are not
copied. Personal kubeconfigs and symlink outputs cannot be overwritten. Keep
the output outside the repository; the generator rejects repository paths. Keep
existing parent directories private too. Do not use shell tracing around
credential operations or print/config-view the generated token file.

The requested duration is one hour; the API server determines the actual expiry,
which the script reports without printing the token. Rerun the same command to
renew. The shared client factory reloads the file on subsequent collections;
an already open client may still fail if its old token expires. There is no
long-lived ServiceAccount Secret and no automatic local refresh.

For FastAPI, these settings can instead be placed in the ignored root `.env`.
The standalone collectors/CLI require exported variables, as above. Do not set
`KUBECONFIG` to the restricted file while minting credentials: token issuance
requires the explicit administrative context. Do not store this file in Git.

```bash
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8005 --no-access-log
# Or use the standalone collector without model credentials:
uv run --locked python diagnostic-service/main.py crash-demo -n aiops
```

Missing/unknown `AIOPS_KUBERNETES_MODE`, missing paths, invalid configs, and
unverified TLS fail closed before client creation. There is no fallback to
`KUBECONFIG`, personal defaults, or the other mode. Expired/revoked tokens are
rejected by Kubernetes with 401: direct evidence returns a safe 503, and agent
tools report an authentication error. Optional collector failures remain partial
evidence; renew credentials rather than granting more permissions.

## Verify the real identity and permissions

```bash
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth whoami
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i get pods -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i list pods -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i get pods/log -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i list events -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i get pods.metrics.k8s.io -n aiops
# Every following check must answer no (exit status 1 is expected):
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i delete pods -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i create pods -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i patch deployments.apps -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i get secrets -n aiops
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i list pods -n kube-system
kubectl --kubeconfig "$AIOPS_KUBECONFIG_PATH" auth can-i get pods/log -n kube-system
```

For impersonation, replace `--kubeconfig ...` with
`--context kind-sentinelops --as=system:serviceaccount:aiops:aiops-agent`.
Impersonation alone does not prove the actual token works; repeat using the
restricted file and perform real Pod/log/event reads. Use server dry-run for any
negative write probes, so an unexpected existing grant cannot mutate workloads.
A missing metrics API or sample is not a permission failure; do not broaden RBAC.

## In-cluster deployment

Set `AIOPS_KUBERNETES_MODE=incluster`, assign `serviceAccountName: aiops-agent`,
and explicitly set `automountServiceAccountToken: true` on the backend Pod (or
supply an appropriate projected API token and CA). The ServiceAccount disables
implicit automount by default. The separate [AIOps Helm chart](helm/aiops/README.md)
provides this Pod wiring, its own ServiceAccount in the release namespace, and a
RoleBinding in the single diagnostic target namespace. It is independent of the
SentinelShop test-application chart and the standalone local identity above.
The client uses `load_incluster_config()` only in this mode, supporting projected
token rotation. It retains certificate verification and never falls back locally.

For a future additional namespace, create the same namespaced Role in that
namespace and a RoleBinding there whose subject remains `aiops/aiops-agent`
for the local identity, or `aiops-system/aiops-agent` for the default Helm release.
Grant that namespace separately in application RBAC. Do not create a ClusterRole
or global binding. This task configures only `aiops`.

## Prior local-credential verification and limitations

The results below were obtained before the repository move using the standalone
local identity. They do not claim that the new Helm release has been deployed.

On Kind `kind-sentinelops`, both impersonation and actual-token checks allowed
all five required read permissions and denied all six prohibited permissions.
Real Pod, Pod-list, log and event reads succeeded. Negative create/delete/patch
requests were denied with server dry-run; Secrets and other-namespace reads were
forbidden. Identity verification returned the exact ServiceAccount username.
Pod metrics were authorized but unavailable; no privileges were expanded.

Authenticated FastAPI direct evidence returned 200 for `crash-demo`. A real
LangGraph run with a scripted model and the actual restricted Kubernetes clients
executed all five tools and completed with 200. Metrics collection reported 404;
other tools collected evidence successfully. The opt-in live collector test also
passed. This verifies the execution path without sending evidence to an external
model; it is not a real-model diagnosis.

Offline verification:

```bash
uv run --locked python -m unittest tests.test_kubernetes_credentials -v
uv run --locked python -m unittest tests.test_auth tests.test_agent_workflow tests.test_backend -v
RUN_K8S_INTEGRATION=0 uv run --locked python -m unittest discover -s tests/diagnostic_service -v
```

Local restricted credentials are a bearer secret: protect the file and renew it.
Namespace-wide `get pods` necessarily permits Kubernetes Pod specifications,
which can contain application-sensitive literals, even though the collectors
allowlist exposed fields. Kubernetes RBAC does not redact log contents. External
model evidence handling, application password policy and other existing review
findings are unchanged by this task. An explicitly supplied privileged kubeconfig
would still be privileged: use the generated file and verify its identity.

References: [TokenRequest workflow](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_token/)
and [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/).
