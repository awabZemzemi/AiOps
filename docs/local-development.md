# Local Ubuntu development

This is the startup guide for FastAPI outside Kubernetes. For a containerized
cluster deployment, use the [AIOps Helm guide](../deploy/aiops/helm/aiops/README.md)
instead. Work from the repository root with Python 3.12 and uv 0.13.8.

## Configure once

```bash
uv sync --locked
test -f .env || cp .env.example .env
chmod 600 .env
```

Set `GROQ_API_KEY` privately in `.env`. Generate a JWT signing key only when one
is not already configured (replacing it would invalidate existing tokens):

```bash
uv run --locked python - <<'PY'
from dotenv import dotenv_values, set_key
import secrets
if not dotenv_values('.env').get('AIOPS_JWT_SECRET'):
    set_key('.env', 'AIOPS_JWT_SECRET', secrets.token_urlsafe(48))
PY
```

Provision and renew restricted Kubernetes credentials using the
[local credential guide](../deploy/aiops/README.md#generate-and-renew-private-local-credentials).
Set `AIOPS_KUBERNETES_MODE=kubeconfig` and `AIOPS_KUBECONFIG_PATH` to that private
file in `.env`. Never point to a developer's privileged kubeconfig. The backend
loads the root `.env`; exported variables take precedence. Neither secret values
nor the private kubeconfig belong in Git.

## Start FastAPI

```bash
uv run --locked alembic upgrade head
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8005 --no-access-log
```

Startup requires valid authentication settings, a migrated database and model
configuration. The graph is built once. `/health` performs no cluster/model call;
Kubernetes credentials are checked when collection is requested. Missing/invalid
credential configuration fails closed, and expired tokens require renewal.

Open http://127.0.0.1:8005/docs or http://127.0.0.1:8005/health. Follow the
[account bootstrap and API guide](authentication.md#first-admin) for access.
Use either local Uvicorn or a Kubernetes port-forward on port 8005, not both.

## Collect evidence without the model

The CLI requires exported Kubernetes settings; it does not load `.env` itself:

```bash
export AIOPS_KUBERNETES_MODE=kubeconfig
export AIOPS_KUBECONFIG_PATH="$HOME/.config/aiops/agent.kubeconfig"
uv run --locked python -m diagnostic_service crash-demo -n aiops --tail-lines 20
```

Choose an existing Pod; the Helm-managed lab Pod is normally named
`sentinelshop-lab-crash-demo`. See the [collector reference](../diagnostic_service/README.md)
for Python imports, evidence fields and bounds. Real investigations send sanitized
prompts and bounded, minimized evidence to the configured external model. The
direct evidence API and CLI use a richer sanitized report. Target names and useful
network diagnostics can be retained. Treat diagnostic output as sensitive because
pattern-based redaction covers recognized formats and configured literals.

## Logs and dependencies

The private `operations.jsonl` log defaults to `logs/backend/`. Configure
`AIOPS_LOG_DIR` for another location. It rotates at 5 MiB with three backups and
records only investigation metadata. The HTTP investigation path disables payload
tracing. Historical transcripts and the separate benchmark artifacts require their
own retention and handling decisions.

`pyproject.toml` and `uv.lock` are the root environment's source of truth. Use
`uv sync --locked` and `uv pip check`; update the lock through uv rather than
freezing an environment. SentinelShop has separate per-service container
requirements and is not a uv workspace member. Offline checks are listed in the
[root README](../README.md#offline-checks).
