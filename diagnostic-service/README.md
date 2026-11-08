# Kubernetes diagnostic evidence collector

Read-only evidence for one Pod, using the official Kubernetes Python client. Reusable tools and a local FastAPI interface expose structured observations. This service does not classify failures, diagnose causes, implement AI agents, or perform remediation.

The existing `test_connection.py` experiment is unchanged. Its Pod/status/log API approach is reused in small functions, without its alert/classification logic. Run the tests below rather than executing the experiment as a test suite: that file connects to the cluster at import time.

## Architecture and project structure

```text
HTTP client → Uvicorn (127.0.0.1) → api.py → diagnostics.py
                                             ↓
                    tools/ → official Kubernetes client → Kubernetes API
```

The synchronous FastAPI routes validate inputs and translate errors. The collector orchestrates the four independent tools; it does not depend on FastAPI. Each request uses the current kubeconfig, and Kubernetes operations remain read-only. Synchronous routes run in FastAPI's worker thread pool because the collector blocks on network I/O.

```text
diagnostic-service/
├── api.py                     # HTTP routes, safe errors, application logging
├── main.py                    # Command-line collector
├── diagnostics.py             # Evidence orchestration
├── kubernetes_client.py        # Kubeconfig and client lifecycle
├── evidence_common.py         # Validation and safe errors
├── models.py                  # JSON conversion and sensitive-field omission
├── requirements.txt           # Runtime dependencies
├── requirements-dev.txt       # Runtime + TestClient transport
├── tools/
│   ├── __init__.py
│   ├── pod_information.py
│   ├── pod_listing.py
│   ├── container_logs.py
│   ├── kubernetes_events.py
│   └── resource_metrics.py
├── tests/                     # Collector, tools, CLI, API, live opt-in tests
└── test_connection.py          # Original exploratory script
```

## Run against kind

From the repository root, create a virtual environment if you do not already have one:

```bash
python3 -m venv diagnostic-service/.venv
diagnostic-service/.venv/bin/python -m pip install -r diagnostic-service/requirements.txt
kubectl config current-context
```

The collector uses the existing kubeconfig (`KUBECONFIG` is respected) and its current context, just like the experiment. For this lab the context should be `kind-sentinelops`. If necessary, select it explicitly with `kubectl config use-context kind-sentinelops` before running the collector.

```bash
diagnostic-service/.venv/bin/python diagnostic-service/main.py crash-demo -n aiops
```

Save the report or adjust log history:

```bash
diagnostic-service/.venv/bin/python diagnostic-service/main.py crash-demo \
  -n aiops --tail-lines 50 > /tmp/crash-demo-evidence.json
```

The CLI accepts a Pod name, `-n/--namespace`, and `--tail-lines`. With no arguments it keeps the existing demo defaults: `aiops/sentinelshop-catalog-84d5859b99-mnghp`, 100 log lines. Supply the current Pod name explicitly after workloads are recreated. The CLI writes JSON to stdout. Optional collection errors remain in the report and exit status is 0. An unreadable/nonexistent target, invalid arguments, or invalid kubeconfig produces a nonzero exit status and a generic error on stderr; raw Kubernetes exception messages are not printed.

For Python callers, run from `diagnostic-service/`:

```python
from diagnostics import collect_pod_evidence

report = collect_pod_evidence("aiops", "crash-demo")
```

## Run FastAPI locally

From the repository root, after installing `requirements.txt`:

```bash
diagnostic-service/.venv/bin/python -m uvicorn api:app \
  --app-dir diagnostic-service --host 127.0.0.1 --port 8000 \
  --log-level info --no-access-log
```

`api:app` imports the `app` object from `api.py`. Keep the explicit loopback host; this API has no user authentication and uses the server process's Kubernetes identity. `--no-access-log` avoids recording arbitrary URL query strings; application logs provide safe evidence-request summaries instead. No kubeconfig is loaded at API import/startup or by `/health`.

Open Swagger UI at http://127.0.0.1:8000/docs. FastAPI generates the OpenAPI specification at `/openapi.json` from the route declarations and validation annotations. There are only two application endpoints; documentation routes are provided by FastAPI.

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Returns `200` with `{"status":"ok"}`; process health, not cluster readiness. |
| `GET /api/v1/pods/{namespace}/{pod_name}/evidence` | Returns the existing evidence report. Optional query: `tail_lines` (default 100, range 1–1000, imported from collector constants). |

```bash
curl -i http://127.0.0.1:8000/health
curl -i 'http://127.0.0.1:8000/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20'
curl -i 'http://127.0.0.1:8000/api/v1/pods/aiops/crash-demo/evidence?tail_lines=0'
```

The last request returns `422` without calling Kubernetes. Namespace and Pod path parameters also validate Kubernetes DNS naming syntax and lengths. These GET endpoints do not accept a request body.

The evidence response has exactly the report sections documented below. Missing optional evidence remains `null`/empty with `collection_errors`, and HTTP status remains `200`. For example, these fields can appear in an otherwise successful report:

```json
{
  "metrics": null,
  "collection_errors": [{
    "operation": "metrics", "status": 404,
    "error_type": "NotFoundException", "message": "Resource unavailable"
  }]
}
```

This is an excerpt, not the full report. Exception type names can vary with client versions.

| HTTP status | Meaning |
| --- | --- |
| `200` | Evidence collected, possibly partially; inspect `collection_errors`. |
| `422` | Invalid path/query input. |
| `403` | Kubernetes forbids reading the target. |
| `404` | Target Pod not found: `{"detail":"Pod not found."}`. |
| `429` | Kubernetes rate limit exceeded. |
| `502` | Kubernetes rejected an otherwise validated evidence request. |
| `503` | Kubeconfig, Kubernetes authentication, transport, or upstream service unavailable. |
| `500` | Unexpected failure: `{"detail":"Evidence collection failed."}`. |

The API maps `PodEvidenceError` to fixed messages and never returns its original text or cause. Kubernetes `401` maps to `503` because the unavailable credentials belong to the service's Kubernetes connection. Unexpected collector errors are caught inside the route, preventing the original exception from being re-raised into Uvicorn's traceback logging.

## Application logging and local security

`api.py` uses Python's built-in `logging`, with a child of Uvicorn's error logger so its existing handler/level can be reused. No logging framework or global configuration is added.

- **INFO:** validated evidence request starts and finishes; records target, log-line limit, elapsed milliseconds, and error count.
- **WARNING:** expected collection failure or a successful report with partial evidence.
- **ERROR:** unexpected internal collection failure, without the exception message or traceback.

Example server output:

```text
INFO: evidence_started namespace=aiops pod=crash-demo tail_lines=20
WARNING: evidence_partial namespace=aiops pod=crash-demo error_count=1
INFO: evidence_completed namespace=aiops pod=crash-demo error_count=1 duration_ms=67.8
```

Only allowlisted metadata is logged. Container log contents, report bodies, collection error messages, credentials, request headers, and raw exception details are not logged. Validation failures are rejected before the endpoint function, so they do not produce application collection logs. Tool functions remain silent; CLI JSON output still uses stdout intentionally.

The service uses `config.load_kube_config(..., persist_config=False)` with the current context and does not overwrite kubeconfig. Its effective permissions are the permissions of that local identity; read-only code does **not** reduce an administrator identity's privileges. Use only trusted kubeconfigs, since they can include credential plugins that execute local programs. The service never retrieves Secret values.

When eventually deployed inside Kubernetes, use a dedicated ServiceAccount with least-privilege RBAC: namespace-scoped `get` on Pods and `pods/log`, `list` on events, and optional `get` on Pod metrics (`metrics.k8s.io`). RBAC defines allowed resource/verb combinations and binds them to identities. In-cluster credential loading, dedicated ServiceAccounts, RBAC manifests, and external API authentication are not provided by this service.

## Independent tools

Each function accepts plain inputs and returns a fresh JSON-serializable dictionary. Tools do not print, modify a caller's report, or call other evidence tools. Each tool can load current kubeconfig on its own; an optional `core_api` or `metrics_api` keyword allows client reuse and offline testing. These are ordinary Python functions callable directly or through a wrapper; no framework is required to use them.

Run Python from `diagnostic-service/`:

```python
from tools.pod_information import collect_pod_information
from tools.pod_listing import list_pods
from tools.container_logs import collect_container_logs
from tools.kubernetes_events import collect_kubernetes_events
from tools.resource_metrics import collect_resource_metrics

information = collect_pod_information("aiops", "crash-demo")
discovery = list_pods("aiops")
logs = collect_container_logs("aiops", "crash-demo", "crashing-container", tail_lines=50)
events = collect_kubernetes_events(
    "aiops", "crash-demo", pod_uid=information["pod"]["uid"]
)
metrics = collect_resource_metrics(
    "aiops", "crash-demo", pod_uid=information["pod"]["uid"]
)
```

| Function | Required parameters | Evidence keys in the returned dictionary |
| --- | --- | --- |
| `collect_pod_information` | `namespace`, `pod_name` | `pod`, `containers`, `collection_errors` |
| `list_pods` | `namespace` | `namespace`, `pods`, `truncated`, `collection_errors` |
| `collect_container_logs` | `namespace`, `pod_name`, `container_name` | `logs` (keyed by container name), `collection_errors` |
| `collect_kubernetes_events` | `namespace`, `pod_name` | `events`, `collection_errors` |
| `collect_resource_metrics` | `namespace`, `pod_name` | `metrics`, `collection_errors` |

Pass `pod_uid` to events/metrics when you have it. Standalone event collection without it uses name filtering and can include earlier Pod instances with the same name. Metrics without it skips the UID comparison. The orchestrator always passes the UID read by the Pod tool.

All tools reject empty targets. The log tool also validates the container name and line bound before opening clients. Optional API failures return `null`/empty evidence plus local errors; the Pod tool raises `PodEvidenceError` when it cannot read the target. Invalid kubeconfig raises that same exception for every tool. The shared error class remains importable from `diagnostics` for compatibility.

`collect_pod_evidence()` retains its signature, report format, limits, redaction, and error ordering. It reads Pod information once, calls the log tool for every declared container, then calls events and metrics, merging each tool's evidence and errors. It shares clients rather than opening a new connection session for each tool.

`list_pods()` is independent of that orchestration. It returns Pod name, namespace,
phase, `ready`, and `restart_count` summaries. Readiness comes from the Pod's
Ready condition; unknown readiness is `null`. Restarts sum the reported regular,
init, and ephemeral container counts; missing container statuses yield `null`.
It requests pages of 50 Pods and follows continuation tokens, returning at most
100 Pods across five requests. Capped or interrupted listings set `truncated`
and include a collection error. A failure before any Pods are collected raises
`PodEvidenceError`; later failures preserve collected evidence. An empty listing
is successful and does not establish whether the namespace itself exists.

For manual discovery inspection from the repository root, run
`.venv/bin/python test_pod_listing.py`. Edit its namespace list directly in code.
It prints structured listings for `aiops` and the normally Pod-free
`kube-node-lease`, tool metadata, and shared registry/ToolNode registrations.
No LLM credentials are required, and it does not create resources.

## Files and API calls

| File | Responsibility |
| --- | --- |
| `kubernetes_client.py` | Loads current kubeconfig into a local configuration and creates/closes the official clients. Sets connection/read timeouts and disables transport retries. |
| `diagnostics.py` | Combines independent tool results into the existing report, using one shared client session. |
| `tools/pod_information.py` | Reads safe Pod metadata and all regular/init/ephemeral container details. |
| `tools/pod_listing.py` | Lists bounded Pod summaries within a namespace, following pagination. Requires `list` permission on Pods. |
| `tools/container_logs.py` | Reads bounded current/previous logs for one named container. |
| `tools/kubernetes_events.py` | Reads bounded events filtered by Pod UID, or name when no UID is supplied. |
| `tools/resource_metrics.py` | Reads optional current CPU/memory usage and checks a supplied Pod UID. |
| `evidence_common.py` | Shared target validation, `PodEvidenceError`, and safe API error formatting. |
| `models.py` | Defines the report's top-level shape and explicitly selects safe fields from Kubernetes models. Converts timestamps to JSON strings. |
| `main.py` | Argparse collector CLI with JSON stdout and safe nonzero errors. |
| `api.py` | Two synchronous HTTP routes, validation, safe status mapping, and logging. |
| `tests/` | Offline mocked tests and an opt-in live integration test. |

The collector performs only these Kubernetes reads:

1. `CoreV1Api.read_namespaced_pod`: `GET /api/v1/namespaces/{namespace}/pods/{pod}`. Provides metadata, specification, and status. A failure here raises `PodEvidenceError`, whose `status` preserves the HTTP code.
2. `CoreV1Api.read_namespaced_pod_log`: `GET .../pods/{pod}/log`, once each for current and previous logs, separately for every regular, init, and ephemeral container. Sets `timestamps=True`, `follow=False`, `tail_lines`, and `limit_bytes`. Missing previous logs are represented by `null` plus a collection error, not an empty string.
3. `CoreV1Api.list_namespaced_event`: `GET .../events` with a field selector for Pod kind and UID. Checks the UID again in returned items, so events for an older Pod with the same name are excluded. Falls back to the Pod name only if no UID was supplied. Uses core/v1 Event fields, including modern event time/series fields.
4. `CustomObjectsApi.get_namespaced_custom_object`: `GET /apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods/{pod}`. Returns per-container CPU/memory quantities as Kubernetes strings. Missing Metrics Server, missing samples, forbidden access, and network failures leave `metrics: null` and add an error.

No exec, patch, update, create, delete, or Secret API calls are made. Required permissions are `get` on `pods` and `pods/log`, `list` on `events`, and optionally `get` on `pods` in `metrics.k8s.io`. RBAC resources are not installed by this task.

## Report contract

The report always contains these sections:

```json
{
  "timestamp": "UTC ISO-8601 collection start time",
  "target": {"namespace": "aiops", "pod": "crash-demo"},
  "pod": {},
  "containers": [],
  "logs": {},
  "events": [],
  "metrics": null,
  "collection_errors": []
}
```

`pod` includes identity/UID, phase, conditions, node/IP, creation time, owners, restart policy, service account, and volume names/types. `containers` joins each declared container to its status by name, with a `type` of `regular`, `init`, or `ephemeral`. It includes image, current state, readiness, restarts, previous termination, CPU/memory requests/limits, probes, and environment variable names. Missing status fields stay `null` rather than claiming the container is unready or has zero restarts.

`logs` maps container names to `{ "current": "...", "previous": null }`. An empty string means a successful read with no output. Each error records the operation, optional container, HTTP status when available, exception type, and a generic message. Raw API error bodies are omitted.

## Bounds and sensitive information

- Default 100 log lines per stream; configurable from 1 to 1,000. Each stream is also capped at 16 KiB, so fewer lines may be returned. The byte cap can cut a log line or its timestamp.
- At most 100 events are collected from one page. A continuation token produces a `CollectionLimit` entry; further pages are not fetched. Events are not guaranteed to be the most recent 100.
- Each request has a 3-second connection timeout and a 10-second read timeout. Calls are sequential; total collection time scales with the number of containers. These are per-request timeouts, not a total report deadline.
- Status and event text fields are capped at 4,096 characters. Truncated text is evidence, not a diagnosis.
- Environment values, volume source details, annotations, and Secret contents are omitted. `envFrom` references are not resolved, so names imported through them cannot be enumerated.
- Probe HTTP header values and all exec command tokens are redacted, since they can embed credentials. Other probe configuration is retained.
- Logs, events, termination messages, and probe URLs are application-controlled text and may themselves contain sensitive information. This collector does not guarantee arbitrary secret detection/redaction in that text. Treat reports as sensitive; avoid logging credentials in applications and review reports before sharing them.

## Tests

Install the development requirements (includes runtime dependencies and HTTPX, which `TestClient` needs):

```bash
diagnostic-service/.venv/bin/python -m pip install -r diagnostic-service/requirements-dev.txt
```

Offline tests use `unittest` and mocked official client models; they do not require cluster access:

```bash
diagnostic-service/.venv/bin/python -m unittest discover -s diagnostic-service/tests -v
```

They cover healthy Pods, CrashLoopBackOff, previous OOMKilled termination, missing previous logs, unavailable metrics, multiple container types, missing/inaccessible Pods, partial API/network failures, bounds, UID event filtering, sensitive-field omission, client configuration, and CLI behavior. Additional independent-tool tests check standalone calls, silence, JSON serialization, local partial errors, default client lifecycle, input validation, and UID matching.

API tests use FastAPI's in-process `TestClient` and patch `api.collect_pod_evidence`; they cover success/report preservation, invalid namespace/Pod/log limits, 404, 403, other upstream statuses, unavailable metrics, safe unexpected errors, worker-thread execution, and generated docs. Logging tests check levels and ensure report/exception contents are excluded. One checks `raise_server_exceptions=True` so unexpected errors cannot escape into the server.

```bash
diagnostic-service/.venv/bin/python -m unittest discover -s diagnostic-service/tests -p test_api.py -v
```

Starlette 1.9.0 emits a deprecation warning for the HTTPX 0.28.1 TestClient transport declared in the development requirements. This is a known dependency compatibility warning, not an application response error.

If `aiops/crash-demo` exists in the current context, enable the read-only integration test:

```bash
RUN_K8S_INTEGRATION=1 diagnostic-service/.venv/bin/python \
  -m unittest discover -s diagnostic-service/tests -v
```

The test never creates a Pod or changes a workload. It verifies that the report is serializable and includes the expected Pod/container; it does not require a particular restart count or transient container state.

## Known technical limitations

The report is a sequence of reads, not an atomic snapshot. A Pod can restart or be recreated between reads; logs and metrics may refer to a later instant. Events and previous logs are subject to Kubernetes retention, and metrics are current samples rather than history. The collector intentionally does not walk owner workloads, inspect Services/nodes, or fetch external configuration.

The API has no user authentication, report persistence, total collection deadline, or application-level concurrency/rate controls. Runtime dependency ranges are not a complete dependency lock. The response uses a generic dictionary schema rather than a detailed response model. Thread pools are finite, and a timed-out caller does not automatically cancel blocking collection. No AI investigation, diagnostic rules, or remediation is implemented.

## Live HTTP verification

Start the local server as described above, select an existing healthy Pod, and request evidence for it and the intentionally crashing Pod. These operations are read-only:

```bash
kubectl -n aiops get pods
healthy_pod=$(kubectl -n aiops get pods -l app=catalog -o jsonpath='{.items[0].metadata.name}')
curl -fsS "http://127.0.0.1:8000/api/v1/pods/aiops/$healthy_pod/evidence?tail_lines=20"
curl -fsS 'http://127.0.0.1:8000/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20'
```

Check the report's `target`, `pod`, `containers`, `logs`, `events`, `metrics`, and `collection_errors`. Healthy-container readiness, crash state, restart counts, event counts, and metrics availability depend on the current cluster state; do not expect fixed values. Optional metrics failure should preserve the other sections and HTTP 200. A missing target should return a safe 404.

To inspect the current identity's required permissions without modifying workloads:

```bash
kubectl -n aiops auth can-i get pods
kubectl -n aiops auth can-i get pods/log
kubectl -n aiops auth can-i list events
```
