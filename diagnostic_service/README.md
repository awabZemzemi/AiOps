# Kubernetes collector reference

`diagnostic_service` is a Python package of read-only evidence collectors. It has
no FastAPI/backend/agent dependency. The HTTP router lives in
`backend/evidence/routes.py`; LangGraph adapters live in `agent/tools/`.

For server startup use [local development](../docs/local-development.md), for
Kubernetes installation use the [AIOps chart guide](../deploy/aiops/helm/aiops/README.md),
and for credentials use [RBAC setup](../deploy/aiops/README.md). These workflows
are maintained in those guides rather than repeated here.

## CLI and Python entry points

From the repository root with explicit Kubernetes settings exported:

```bash
uv run --locked python -m diagnostic_service crash-demo -n aiops --tail-lines 20
uv run --locked python -m diagnostic_service.check_connection
```

The second command is an explicit manual cluster check, not an automated test.
For Python callers:

```python
from diagnostic_service.diagnostics import collect_pod_evidence
report = collect_pod_evidence("aiops", "crash-demo", tail_lines=20)
```

## Independent tools

Each function accepts plain inputs and returns a fresh JSON-serializable dictionary. Tools do not print, modify a caller's report, or call other evidence tools. Each tool uses the shared explicit credential factory; an optional `core_api` or `metrics_api` keyword allows client reuse and offline testing. These are ordinary Python functions callable directly or through a wrapper; no framework is required to use them.

Run Python from the repository root:

```python
from diagnostic_service.tools.pod_information import collect_pod_information
from diagnostic_service.tools.pod_listing import list_pods
from diagnostic_service.tools.container_logs import collect_container_logs
from diagnostic_service.tools.kubernetes_events import collect_kubernetes_events
from diagnostic_service.tools.resource_metrics import collect_resource_metrics

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

All tools reject empty targets. The log tool also validates the container name and line bound before opening clients. Optional API failures return `null`/empty evidence plus local errors; the Pod tool raises `PodEvidenceError` when it cannot read the target. Invalid kubeconfig raises that same exception for every tool. The shared error class remains importable from `diagnostic_service.diagnostics` for compatibility.

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

For discovery through the agent, send `{"message":"List Pods in namespace aiops."}`
to `POST /agent/investigate`. For direct Python access without model credentials,
call `list_pods("aiops")` from the repository root, as shown above.

## Files and API calls

| File | Responsibility |
| --- | --- |
| `kubernetes_client.py` | Loads explicitly selected restricted or in-cluster credentials into a local configuration and creates/closes the official clients. Sets connection/read timeouts and disables transport retries. |
| `diagnostics.py` | Combines independent tool results into the existing report, using one shared client session. |
| `tools/pod_information.py` | Reads safe Pod metadata and all regular/init/ephemeral container details. |
| `tools/pod_listing.py` | Lists bounded Pod summaries within a namespace, following pagination. Requires `list` permission on Pods. |
| `tools/container_logs.py` | Reads bounded current/previous logs for one named container. |
| `tools/kubernetes_events.py` | Reads bounded events filtered by Pod UID, or name when no UID is supplied. |
| `tools/resource_metrics.py` | Reads optional current CPU/memory usage and checks a supplied Pod UID. |
| `evidence_common.py` | Shared target validation, `PodEvidenceError`, and safe API error formatting. |
| `models.py` | Defines the report's top-level shape and explicitly selects safe fields from Kubernetes models. Converts timestamps to JSON strings. |
| `main.py` | Argparse collector CLI with JSON stdout and safe nonzero errors. |
| `../backend/evidence/routes.py` | HTTP routes and authorization, outside the collector package. |
| `../tests/collectors/` | Offline mocked tests and an opt-in live integration test. |

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

```bash
RUN_K8S_INTEGRATION=0 uv run --locked python -m unittest discover -s tests/collectors -v
```

The suite uses mocked Kubernetes clients and temporary application databases.
Live collection is opt-in via `RUN_K8S_INTEGRATION=1` and requires explicit valid
credentials. It is not part of offline CI.

## Known technical limitations

The report is a sequence of reads, not an atomic snapshot. A Pod can restart or be recreated between reads; logs and metrics may refer to a later instant. Events and previous logs are subject to Kubernetes retention, and metrics are current samples rather than history. The collector intentionally does not walk owner workloads, inspect Services/nodes, or fetch external configuration.

The API has persistent users, JWT authentication and namespace authorization; login rate limiting is per process. It has no report persistence, total collection deadline, or investigation admission/concurrency controls. See the [authentication guide](../docs/authentication.md) for deployment limitations. Root Python dependencies are locked with uv. The evidence response uses a generic dictionary schema rather than a detailed response model. Thread pools are finite, and a timed-out caller does not automatically cancel blocking collection. The collector has no diagnostic rules or remediation; AI investigation is provided separately by the agent endpoint in the same backend.

