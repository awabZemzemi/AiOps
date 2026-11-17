# AIOps Helm chart

This chart deploys the diagnostic backend independently of the SentinelShop test
application. It contains one backend Deployment, ClusterIP Service, retained user
SQLite PVC, ServiceAccount, and read-only diagnostic Role/RoleBinding.

- Test application: `app/` images and `deploy/test-lab/helm/sentinelshop`, installed into `aiops`.
- AIOps: `backend/Dockerfile` and this chart, installed into `aiops-system`.
- The AIOps ServiceAccount lives in `aiops-system`; its Role/RoleBinding live in
  the existing target namespace `aiops`. It can inspect that namespace only.
- The backend keeps the existing unified API, agent loop and five read-only tools.
  Kubernetes collectors are part of its image; they are not another HTTP service.

## Build and load into Kind

Run from the repository root:

```bash
docker build -f backend/Dockerfile -t aiops-backend:local .
kind load docker-image aiops-backend:local --name sentinelops
```

The multi-stage image uses locked runtime dependencies, Python 3.12, and a nonroot
UID/GID 10001. The allowlisted `.dockerignore` excludes local environment files,
credentials, databases, Git history, logs, tests and the SentinelShop source.
Use a new immutable image tag for subsequent builds and set `image.tag` on upgrade.
Base images are pinned by digest; update those pins through your image-maintenance workflow.

## Create private runtime configuration

The chart references an existing Secret; it never renders credentials into Helm
values or manifests. The Secret must contain `AIOPS_JWT_SECRET` (a strong random
value of at least 32 bytes) and `GROQ_API_KEY`. Keep the same JWT secret across
restarts/upgrades, unless intentionally invalidating existing access tokens.

For local development, copy these two values from your existing private root
`.env` into a temporary private file, then create the Secret without putting
values in command-line arguments:

```bash
kubectl --context kind-sentinelops create namespace aiops-system
(
set -eu
umask 077
secret_file=$(mktemp)
trap 'rm -f "$secret_file"' EXIT
uv run --locked python -c '
from dotenv import dotenv_values
values = dotenv_values(".env")
for name in ("AIOPS_JWT_SECRET", "GROQ_API_KEY"):
    value = values.get(name, "")
    if not value or "\n" in value or "\r" in value:
        raise SystemExit("Required private setting is missing or invalid")
for name in ("AIOPS_JWT_SECRET", "GROQ_API_KEY"):
    print(name + "=" + values[name])
' > "$secret_file"
kubectl --context kind-sentinelops -n aiops-system create secret generic \
  aiops-backend-secrets --from-env-file="$secret_file"
)
```

Use an external secret-management workflow for production. Do not commit the
private file or use `--set` for secrets. Namespace/Secret creation is a one-time
step: reuse the existing namespace/Secret when upgrading. Avoid shell tracing.

## Install independently

The configured diagnostic target namespace must exist, but no Test Lab is required.
For the optional test workloads, see [the independent lab guide](../../../test-lab/README.md).
Create namespace `aiops` separately if absent, or select an existing namespace with
`--set rbac.targetNamespace=...`. Then:

```bash
helm upgrade --install aiops ./deploy/aiops/helm/aiops \
  --kube-context kind-sentinelops --namespace aiops-system --create-namespace \
  --wait --timeout 5m
kubectl --context kind-sentinelops -n aiops-system \
  port-forward service/aiops-backend 8005:8005
```

Open http://127.0.0.1:8005/docs. The entry point is `backend.main:app`.
An init container applies Alembic migrations before startup. The backend loads
in-cluster projected ServiceAccount credentials; no local kubeconfig or static
ServiceAccount token is copied into the image or chart. The Pod explicitly enables
token mounting because the ServiceAccount disables implicit mounting by default.

Create the first Admin with a hidden password prompt, once:

```bash
kubectl --context kind-sentinelops -n aiops-system exec -it \
  deployment/aiops-backend -- python -m backend.auth.bootstrap \
  --username admin --namespace aiops
```

Follow the [authentication guide](../../../../docs/authentication.md) for login and API
requests. Application namespace permissions are independent of Kubernetes RBAC;
bootstrapping an Admin without `--namespace aiops` grants no diagnostic access.
No default account is created.

## Values, security and persistence

See `values.yaml` for image, resources, existing Secret name, ServiceAccount name,
service port, storage size/class and the single diagnostic target namespace.
`rbac.create=false` supports externally managed grants; provision the same narrow
Role/RoleBinding yourself. It does not broaden access. No ClusterRole,
ClusterRoleBinding, writes, Secrets reads, or wildcard permissions are included.
The rules exactly match [the existing collector requirements](../../README.md).

The Role names include the Helm release prefix, avoiding ownership conflicts
with the standalone local-development RBAC manifest. That older identity is
separate; this chart does not adopt or delete it. Do not add unrelated grants to
the chart's ServiceAccount. The Helm installer needs privileges to create a
namespaced RoleBinding in the target namespace; the running backend does not.

The container has a read-only root filesystem, drops all Linux capabilities,
disables privilege escalation, and uses the default seccomp profile. Database,
temporary files and logs have explicit writable volumes. `/logs/backend` is
created by the nonroot application beneath the mounted volume. Logs are bounded
and ephemeral across Pod recreation; the user database is persistent.

SQLite and the per-process login limiter require one backend replica and one
Uvicorn worker. The Deployment uses Recreate so old/new Pods do not compete for
SQLite storage or migrations. This causes brief downtime during updates. Back
up the PVC before schema upgrades; an image rollback does not undo a migration.
Do not scale manually without moving to a shared database and shared admission
controls. A default StorageClass is required unless a storage class is configured.

The PVC has Helm's keep policy: uninstall retains identity data. Reinstalling the
same release reuses it. Deleting the namespace or Kind cluster destroys local
storage. Permanent data removal requires an explicit PVC deletion after backup.
The external Secret is not owned by Helm and is retained independently.

## Verify and uninstall separately

```bash
helm lint ./deploy/aiops/helm/aiops
helm template aiops ./deploy/aiops/helm/aiops -n aiops-system
uv run --locked python -m unittest tests.test_aiops_chart -v
kubectl --context kind-sentinelops -n aiops-system get pods,svc,pvc
kubectl --context kind-sentinelops auth can-i get pods -n aiops \
  --as=system:serviceaccount:aiops-system:aiops-agent
kubectl --context kind-sentinelops auth can-i get secrets -n aiops \
  --as=system:serviceaccount:aiops-system:aiops-agent
helm uninstall aiops -n aiops-system --kube-context kind-sentinelops
```

The checks should answer yes for required diagnostic reads and no for Secrets,
writes and other namespaces. Test `/health` without invoking the external model.
A real investigation sends permitted workload evidence to the configured model;
existing evidence-handling and password-policy limitations remain unchanged.

Uninstalling AIOps removes its diagnostic Role/RoleBinding but does not remove
SentinelShop. Uninstalling SentinelShop does not remove AIOps. Neither chart has
a dependency on the other; workload and diagnostic identities stay separate.
