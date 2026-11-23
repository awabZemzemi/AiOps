# AIOps

A reactive, read-only Kubernetes diagnostic application. FastAPI authenticates
users, enforces roles and namespace permissions, and runs a shared LangGraph
agent over Kubernetes evidence collectors. SentinelShop is a separate Test Lab
for healthy and intentionally failing workloads. No remediation is implemented.

## Choose a workflow

| Workflow | Authoritative guide |
| --- | --- |
| Run FastAPI directly on Ubuntu | [Local development](docs/local-development.md) |
| Deploy AIOps inside Kubernetes | [AIOps Helm guide](deploy/aiops/helm/aiops/README.md) |
| Bootstrap accounts, log in and use the API | [Authentication and API access](docs/authentication.md) |
| Configure local restricted credentials and inspect RBAC ownership | [Kubernetes credentials and RBAC](deploy/aiops/README.md) |
| Deploy/remove SentinelShop and crash-demo | [Test Lab](deploy/test-lab/README.md) |
| Create a new local Kind cluster | [Kind infrastructure](infra/kind/README.md) |
| Use collectors directly or inspect evidence contracts | [Collector reference](diagnostic_service/README.md) |
| Run one bounded diagnostic incident manually | [AiOps Benchmark v1](benchmark/README.md) |
| Run the SentinelShop Docker Compose lab | [Test application](app/README.md) |

The AIOps and Test Lab Helm releases are independent. Installing a chart is a
separate operation from creating its files. The deployment guides list required
images, namespaces and private Secrets before installation.

## Repository layout

```text
agent/                         # Reactive graph, state and authorized LLM adapters
backend/                       # FastAPI, identities, evidence and investigation routes
  auth/                        # JWTs, users, role and namespace dependencies
  core/                        # Lifecycle, safe errors and private logging
  evidence/                    # Direct evidence HTTP router
  investigations/              # Agent HTTP router, schemas and telemetry
diagnostic_service/            # Importable read-only collectors and CLI; no HTTP dependency
benchmark/                     # Manual, resource-constrained incident benchmark
app/                           # SentinelShop source, Dockerfiles and Compose configuration
tests/                         # All automated tests; explicit manual integration runner
  collectors/                  # Collector, CLI and evidence API tests
deploy/
  aiops/helm/aiops/             # Supported Kubernetes deployment for AIOps
  aiops/manifests/              # Local restricted-credential provisioning only
  test-lab/helm/sentinelshop/   # Supported Kubernetes deployment for the Test Lab
  test-lab/manifests/           # Alternative raw lab examples; not Helm-owned resources
infra/kind/                    # Optional local cluster configuration
migrations/                    # Alembic schema changes
scripts/                       # Private ServiceAccount credential generation
docs/                          # Local development and authentication guides
```

## Runtime boundaries

```text
HTTP → backend authentication/authorization
     → evidence route → diagnostic_service collectors → Kubernetes
     → investigation route → agent ⇄ authorized tools → diagnostic_service collectors
```

The backend owns HTTP routing and authorization. Collectors use qualified package
imports and do not depend on FastAPI, the backend, or LangGraph. Agent tools adapt
collector results and enforce trusted namespace context before collection.
Application roles are independent of Kubernetes ServiceAccount permissions.

## Offline checks

Use CPython 3.12 and uv 0.13.8, matching CI. From the repository root:

```bash
uv sync --locked
uv pip check
RUN_K8S_INTEGRATION=0 uv run --locked python -m unittest discover -s tests -v
helm lint deploy/aiops/helm/aiops
helm lint deploy/test-lab/helm/sentinelshop
helm template aiops deploy/aiops/helm/aiops -n aiops-system
helm template sentinelshop-lab deploy/test-lab/helm/sentinelshop -n aiops
```

[CI](.github/workflows/ci.yml) runs offline Python and Helm checks without model
credentials or cluster access. The live collector test is explicitly opt-in; the
manual Compose integration runner changes demo containers/stock and is excluded
from automatic execution. Keep secrets, local databases and logs outside Git.
