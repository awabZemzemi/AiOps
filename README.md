# AiOps

A local Kubernetes/SRE learning project: SentinelShop provides healthy and intentionally failing workloads, and `diagnostic-service` collects read-only Pod evidence through reusable Python tools, a CLI, and a local FastAPI interface.

## Current architecture

```text
HTTP client → FastAPI → evidence collector → four diagnostic tools → Kubernetes API
Python/CLI callers ────────────┘
```

The collector returns Pod/container details, bounded current/previous logs, related events, optional resource metrics, and collection errors. It does not diagnose root causes or modify Kubernetes resources.

| Directory | Purpose |
| --- | --- |
| `app/` | SentinelShop microservices and Docker Compose lab. |
| `k8s/` | Raw Kubernetes manifests, including the intentional crash-demo Pod. |
| `helm/sentinelshop/` | Configurable Helm deployment for the shop. |
| `diagnostic-service/` | Evidence collector, four independent tools, HTTP/CLI interfaces, tests, and detailed documentation. |

## Run and test the diagnostic service

From this repository root:

```bash
python3 -m venv diagnostic-service/.venv
diagnostic-service/.venv/bin/python -m pip install -r diagnostic-service/requirements-dev.txt
kubectl config current-context
diagnostic-service/.venv/bin/python -m uvicorn api:app \
  --app-dir diagnostic-service --host 127.0.0.1 --port 8000 \
  --log-level info --no-access-log
```

The lab uses context `kind-sentinelops` and namespace `aiops`. The API uses the server process's current kubeconfig identity. Keep it bound to loopback: it has no user authentication. Do not share evidence reports without reviewing their application-controlled text for sensitive content.

```bash
curl -i http://127.0.0.1:8000/health
curl -i 'http://127.0.0.1:8000/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20'
diagnostic-service/.venv/bin/python -m unittest discover -s diagnostic-service/tests -v
```

Swagger UI is at http://127.0.0.1:8000/docs. `/health` checks the process; evidence requests return the existing report, including partial collection errors. Invalid inputs return 422; a nonexistent target returns 404. See the [diagnostic-service README](diagnostic-service/README.md) for the full endpoint/error contract, architecture, security decisions, standalone tool examples, and live testing commands.

## Technical limitations

This is a local development API with no user authentication or in-cluster credential setup. Collection uses sequential reads rather than an atomic snapshot, and per-request Kubernetes timeouts do not impose an overall collection deadline. The service README documents resource bounds, sensitive-text handling, and the known TestClient transport deprecation warning.

Further lab documentation: [application](app/README.md), [Kubernetes](k8s/README.md), [Helm chart](helm/sentinelshop/README.md).
