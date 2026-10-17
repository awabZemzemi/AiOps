# Kubernetes diagnostic evidence collector

Read-only evidence for one Pod, using the official Kubernetes Python client. This module gathers observations; it does not classify failures, diagnose causes, perform remediation, or expose HTTP endpoints.

The existing `test_connection.py` experiment is unchanged. Its Pod/status/log API approach is reused in small functions, without its alert/classification logic. Run the tests below rather than executing the experiment as a test suite: that file connects to the cluster at import time.

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

With no arguments, the CLI keeps the demo defaults: `aiops/crash-demo`, 100 log lines. The CLI writes JSON to stdout. Optional collection errors remain in the report and exit status is 0. An unreadable/nonexistent target, invalid arguments, or invalid kubeconfig produces a nonzero exit status and an error on stderr.

For Python callers, run from `diagnostic-service/`:

```python
from diagnostics import collect_pod_evidence

report = collect_pod_evidence("aiops", "crash-demo")
```

## Independent tools

Each function accepts plain inputs and returns a fresh JSON-serializable dictionary. Tools do not print, modify a caller's report, or call other evidence tools. Each tool can load current kubeconfig on its own; an optional `core_api` or `metrics_api` keyword allows client reuse and offline testing. These are ordinary Python functions that a later API or agent wrapper can call; no framework is required.

Run Python from `diagnostic-service/`:

```python
from tools.pod_information import collect_pod_information
from tools.container_logs import collect_container_logs
from tools.kubernetes_events import collect_kubernetes_events
from tools.resource_metrics import collect_resource_metrics

information = collect_pod_information("aiops", "crash-demo")
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
| `collect_container_logs` | `namespace`, `pod_name`, `container_name` | `logs` (keyed by container name), `collection_errors` |
| `collect_kubernetes_events` | `namespace`, `pod_name` | `events`, `collection_errors` |
| `collect_resource_metrics` | `namespace`, `pod_name` | `metrics`, `collection_errors` |

Pass `pod_uid` to events/metrics when you have it. Standalone event collection without it uses name filtering and can include earlier Pod instances with the same name. Metrics without it skips the UID comparison. The orchestrator always passes the UID read by the Pod tool.

All tools reject empty targets. The log tool also validates the container name and line bound before opening clients. Optional API failures return `null`/empty evidence plus local errors; the Pod tool raises `PodEvidenceError` when it cannot read the target. Invalid kubeconfig raises that same exception for every tool. The shared error class remains importable from `diagnostics` for compatibility.

`collect_pod_evidence()` retains its signature, report format, limits, redaction, and error ordering. It reads Pod information once, calls the log tool for every declared container, then calls events and metrics, merging each tool's evidence and errors. It shares clients rather than opening a new connection session for each tool.

## Files and API calls

| File | Responsibility |
| --- | --- |
| `kubernetes_client.py` | Loads current kubeconfig into a local configuration and creates/closes the official clients. Sets connection/read timeouts and disables transport retries. |
| `diagnostics.py` | Combines independent tool results into the existing report, using one shared client session. |
| `tools/pod_information.py` | Reads safe Pod metadata and all regular/init/ephemeral container details. |
| `tools/container_logs.py` | Reads bounded current/previous logs for one named container. |
| `tools/kubernetes_events.py` | Reads bounded events filtered by Pod UID, or name when no UID is supplied. |
| `tools/resource_metrics.py` | Reads optional current CPU/memory usage and checks a supplied Pod UID. |
| `evidence_common.py` | Shared target validation, `PodEvidenceError`, and safe API error formatting. |
| `models.py` | Defines the report's top-level shape and explicitly selects safe fields from Kubernetes models. Converts timestamps to JSON strings. |
| `main.py` | Small argparse CLI; no web framework. |
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

Offline tests use `unittest` and mocked official client models; they do not require cluster access:

```bash
diagnostic-service/.venv/bin/python -m unittest discover -s diagnostic-service/tests -v
```

They cover healthy Pods, CrashLoopBackOff, previous OOMKilled termination, missing previous logs, unavailable metrics, multiple container types, missing/inaccessible Pods, partial API/network failures, bounds, UID event filtering, sensitive-field omission, client configuration, and CLI behavior. Additional independent-tool tests check standalone calls, silence, JSON serialization, local partial errors, default client lifecycle, input validation, and UID matching.

If `aiops/crash-demo` exists in the current context, enable the read-only integration test:

```bash
RUN_K8S_INTEGRATION=1 diagnostic-service/.venv/bin/python \
  -m unittest discover -s diagnostic-service/tests -v
```

The test never creates a Pod or changes a workload. It verifies that the report is serializable and includes the expected Pod/container; it does not require a particular restart count or transient container state.

## Limits and next task

The report is a sequence of reads, not an atomic snapshot. A Pod can restart or be recreated between reads; logs and metrics may refer to a later instant. Events and previous logs are subject to Kubernetes retention, and metrics are current samples rather than history. The collector intentionally does not walk owner workloads, inspect Services/nodes, or fetch external configuration.

The next task can add the service/API interface around `collect_pod_evidence` and define report access/storage policies. AI investigation, diagnostic rules, and remediation remain separate future work.
