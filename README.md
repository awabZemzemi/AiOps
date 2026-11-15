# AIOps

A local Kubernetes investigation lab. A FastAPI backend runs a LangGraph agent that gathers read-only evidence and returns a Markdown diagnosis. SentinelShop, a small demo application, provides workloads for practicing outages and troubleshooting.

The project combines Pod discovery, container logs, Kubernetes events, and resource metrics in one investigation. It does not perform automated remediation.

## Quick start

Use **uv 0.13.8** and **CPython 3.12** (pinned to **3.12.3** in `.python-version`), a Groq API key, and a kubeconfig with read access to the workloads you want to inspect. Resource metrics also require a working Kubernetes Metrics API.

Install uv using the [official installation guide](https://docs.astral.sh/uv/getting-started/installation/). For Linux/macOS:

```bash
curl -LsSf https://astral.sh/uv/0.13.8/install.sh | sh
```

Open a new shell or add the installer's binary directory to `PATH` before running `uv`.

Run from the repository root:

```bash
uv sync --locked
cp .env.example .env
```

Set `GROQ_API_KEY` in `.env`, then check your Kubernetes context and start the backend:

```bash
kubectl config current-context
uv run --locked uvicorn backend.main:app \
  --host 127.0.0.1 --port 8005 --no-access-log
```

The backend loads the root `.env` at startup; exported environment variables take precedence. It builds the agent graph once and reuses it across independent requests. Missing model configuration prevents startup. Kubernetes connections are opened when evidence is requested.

`pyproject.toml` is the source of truth for direct dependencies; `uv.lock` records resolved transitive versions and artifact hashes. This is one root application project with `package = false`, so imports and entry points use the existing source tree. `uv sync --locked` creates/manages the ignored `.venv` and includes the `dev` test group; `uv sync --locked --no-dev` installs runtime dependencies only. A stale lock causes `--locked` to fail rather than update versions. Activation is unnecessary with `uv run --locked`.

Migration preserved all 68 tested Linux runtime versions, using the previous requirements pins as temporary resolver constraints. Those constraints were removed after seeding the lock; transitive versions are now maintained only in `uv.lock`. The universal lock also contains platform-specific dependencies that are not installed on Linux. Runtime behavior is verified on Linux x86_64/Python 3.12.3; other platforms require testing. Existing unrelated packages in `.venv` are removed by an exact sync, so use a dedicated project environment.

The redundant root requirements files were removed after their documentation consumers were migrated. SentinelShop's `app/*/requirements.txt` files remain because its independent Docker build contexts consume them; they are not uv workspace projects.

Open [interactive API docs](http://127.0.0.1:8005/docs) or check the process:

```bash
curl http://127.0.0.1:8005/health
# {"status":"ok"}
```

The API uses your server process's Kubernetes identity and has no user authentication. Keep the local server bound to `127.0.0.1`.

## Investigate a workload

Include the namespace and, when known, the Pod name:

```bash
curl -sS http://127.0.0.1:8005/agent/investigate \
  -H 'Content-Type: application/json' \
  -d '{"message":"Check Pod crash-demo in namespace aiops. Explain the evidence and suggest next steps."}'
```

If you do not know the Pod name, ask `List Pods in namespace aiops.` first. Requests are independent; the endpoint does not retain a conversation between requests.

An example response:

```json
{
  "investigation_id": "e79a859e-3ea9-4b67-a672-924f4608f983",
  "status": "completed",
  "answer": "## Diagnosis\n\nThe container is restarting.\n\n**Evidence:** ...",
  "metadata": {
    "duration_ms": 1248.73,
    "tools_used": ["get_pod_info", "get_logs", "get_events"],
    "tool_call_count": 3
  }
}
```

Responses are validated with Pydantic. The `answer` preserves the final model message's full Markdown, including lists, code blocks, and line breaks. Timing and tool metadata are computed by the backend, independently of what the model writes.

| Field | Meaning |
| --- | --- |
| `investigation_id` | A new UUID for each accepted investigation, shared by its response and logs. |
| `status` | `completed` when a final answer is produced; `failed` on an investigation error. A completed diagnosis can still describe unhealthy workloads or unavailable evidence. |
| `answer` | The full diagnostic Markdown on success, or a fixed safe error explanation on failure. |
| `metadata.duration_ms` | Monotonic elapsed time from investigation start through graph execution and answer extraction, before log persistence. |
| `metadata.tools_used` | Unique tool names from structured LangGraph messages, in first-use order. |
| `metadata.tool_call_count` | Requested tool calls, including repeated calls and calls that fail. Matching tool results are not counted twice. |

| HTTP status | Behavior |
| --- | --- |
| `200` | Returns the completed investigation. |
| `422` | Rejects missing, blank, or non-string messages before running the graph; uses FastAPI's validation response. |
| `503` | Graph step limit reached; returns `status: "failed"` and the metadata collected so far. |
| `500` | Investigation failed; returns `status: "failed"` and a generic explanation without raw exception details. |

The `500` and `503` responses use the same four-field schema as successful investigations. Partial evidence stays available in the local debugging history.

## Evidence without agent reasoning

The same backend exposes the existing collectors directly:

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Process health; does not query the model or cluster. |
| `GET /api/v1/pods/{namespace}/{pod_name}/evidence` | Structured Pod/container details, logs, events, metrics, and collection errors. |
| `POST /agent/investigate` | Agent-led investigation and diagnostic answer. |

```bash
curl -sS 'http://127.0.0.1:8005/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20'
```

Direct CLI collection does not require model credentials:

```bash
uv run --locked python diagnostic-service/main.py crash-demo -n aiops --tail-lines 20
```

See the [collector documentation](diagnostic-service/README.md) for evidence fields, bounds, error handling, and direct Python usage.

## Local investigation logs

Logging uses Python's built-in `logging` module and stays in `backend/logging_setup.py`, separate from agent reasoning. By default, private logs are written to `logs/backend/`:

| File | Contents |
| --- | --- |
| `operations.jsonl` | JSONL start, completion, and error events with investigation ID, status, elapsed time, tool names, and call count. Errors include their type and stack locations. |
| `messages.jsonl` | JSON records containing the complete serialized LangGraph message history, including tool arguments, tool results, and model metadata, after redaction. |

The endpoint consumes graph state updates so a later model or tool failure does not discard previously emitted messages. Failures and cancellation run the same logging cleanup. If a log write fails, a safe JSON error is emitted to stderr and the investigation response is preserved.

Both streams rotate at **5 MiB per file**, retaining **three backups each**: at most **40 MiB** across the two streams. Oversized records are split into numbered `investigation_log_fragment` JSONL entries; concatenate their `json_fragment` strings in `part` order and parse the resulting JSON to reconstruct the record. Retention applies to fragments too, so histories exceeding the retained capacity cannot be kept in full.

Directories are owner-only (`0700`) and log files are owner-readable/writable (`0600`). Logs and their rotated backups are excluded from Git. Run one server process per log directory; shared-file rotation across multiple workers is not supported.

Before persistence, the logger redacts sensitive structured fields, JSON embedded in message content, common credential formats, authorization headers, URL credentials, email addresses, IPv4 addresses, and values from credential-related environment variables. Raw exception messages and local variables are omitted. Redaction affects stored copies; the diagnostic answer returned by the API is preserved.

Optional settings are documented in `.env.example`:

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Required model credential. |
| `AIOPS_LOG_DIR` | Overrides the log directory. Keep custom paths private and outside version control. |
| `AIOPS_LOG_REDACT_VALUES` | Additional sensitive literals, separated by newlines, to remove from persisted logs. Use this for project-specific identifiers or secrets that generic patterns cannot recognize. |

Application-controlled text can contain sensitive information outside these patterns. Review diagnostic responses before sharing them. No external tracing service is enabled; the investigation ID is passed through LangGraph execution metadata for future tracing integrations.

## Architecture

```text
HTTP client → FastAPI backend
                ├── /health
                ├── /api/v1/pods/.../evidence → diagnostic collectors
                └── /agent/investigate → LangGraph agent ⇄ ToolNode
                                               ↓
                                      diagnostic collectors → Kubernetes API

Investigation lifecycle → private operational logs + redacted message history
```

The graph follows `START → agent → tools → agent` until the model returns a final answer. State contains only messages, merged with LangGraph's `add_messages` reducer. The default limit is 16 graph steps, counting both agent and tool steps.

The current model profile is `gpt_oss_120b` on Groq, configured in `agent/factory/model.py`. The five tools reuse existing collectors: `list_pods`, `get_pod_info`, `get_logs`, `get_events`, and `get_metrics`. Tool arguments explicitly identify the namespace, Pod, and container; observations retain those identities and partial collection errors.

| Path | Responsibility |
| --- | --- |
| `backend/main.py` | Unified FastAPI app, startup lifecycle, validated responses, and investigation orchestration. |
| `backend/logging_setup.py` | Centralized JSON logging, redaction, permissions, and rotation. |
| `agent/` | Graph, message state, model factory, prompt, and tool adapters. |
| `diagnostic-service/` | Reusable collectors, CLI, evidence API router, and collector tests. |
| `app/` | SentinelShop services and Docker Compose lab. |
| `k8s/` | Raw Kubernetes manifests and the intentional crash-demo workload. |
| `helm/sentinelshop/` | Configurable Helm chart and rendering tests. |
| `test_agent_workflow.py` | Offline graph and tool regression tests. |
| `test_backend.py` | Offline API, logging, and graph integration tests. |

## Tests

Create a development environment and install the locked dependencies:

```bash
uv sync --locked
uv lock --check
uv pip check
```

Run the offline suites explicitly from the repository root:

```bash
uv run --locked python -m unittest test_agent_workflow test_backend -v
RUN_K8S_INTEGRATION=0 uv run --locked python -m unittest discover -s diagnostic-service/tests -v
uv run --locked python -m unittest discover -s helm/sentinelshop/tests -v
```

These tests mock model and Kubernetes calls. They check the real graph/tool loop, successful and failed API responses, Markdown preservation, tool metadata, retained failure history, redaction, private permissions, rotation, and logging failures. They do not require a real API key or a running cluster. The live Kubernetes test is skipped unless `RUN_K8S_INTEGRATION=1` is set.

Chart tests require the external Helm binary (tested with v3.18.4); PyYAML, HTTPX, and Starlette are explicit `dev` dependencies. They also occur in the runtime dependency graph. Tests use the standard library's `unittest`; pytest is not currently required.

The Docker integration script in `app/tests/` exercises a running Compose lab and changes its workloads. Follow the [application guide](app/README.md) for that separate test setup. The exploratory `diagnostic-service/test_connection.py` also accesses a real cluster; use the explicit suite commands above rather than running it as a test.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pushes to `main` and `develop`, and pull requests targeting `main`. One Ubuntu 24.04 job installs uv 0.13.8, the Python version from `.python-version`, and Helm v3.18.4. It uses the official `astral-sh/setup-uv` action with a dependency cache keyed to `uv.lock`, `pyproject.toml`, and `.python-version`. Every run still executes `uv sync --locked` and checks dependency compatibility; the cache never replaces lockfile validation. Actions are pinned to immutable commits.

The job checks Python syntax, runs the agent, backend, diagnostic, and chart test suites above with verbose output, and executes `helm lint ./helm/sentinelshop` and `helm template sentinelshop ./helm/sentinelshop -n aiops`. Any failed setup, check, or test fails the job. Helm renders locally without contacting Kubernetes.

CI grants only `contents: read`, uses no repository secrets, clears the model API key, sets `KUBECONFIG=/dev/null`, disables tracing, and fixes `RUN_K8S_INTEGRATION=0`. The read-only live Kubernetes test remains explicitly opt-in outside CI; the Docker lab integration and exploratory cluster script are also manual. Normal CI makes no paid model calls, deploys nothing, and modifies no cluster resources.

After committing and pushing the workflow together with `pyproject.toml`, `.python-version`, and `uv.lock`, open **GitHub → Actions → CI** to inspect the run and individual step logs. Pull requests also show the **Offline tests** check. GitHub Actions must be enabled for the repository; fork PRs may require maintainer approval. The workflow has no deployment stage.

## Managing dependencies

Add a runtime dependency with `uv add 'package-name==X.Y.Z'`, or a test-only dependency with `uv add --dev 'package-name==X.Y.Z'`, replacing the placeholders with the selected package/version. Use `uv remove package-name` or `uv remove --dev package-name` to remove one.

Direct dependencies are deliberately pinned. To update one, use `uv add 'langgraph==X.Y.Z'` with an intentionally chosen version. For a transitive dependency, use `uv lock --upgrade-package 'package-name==X.Y.Z'` within the existing dependency constraints. Avoid blanket `uv lock --upgrade`; normal locking retains compatible existing versions.

Review changes to both `pyproject.toml` and `uv.lock`, run `uv sync --locked`, `uv pip check`, and all three offline suites, then verify installation in a fresh environment before accepting an update. Do not hand-edit `uv.lock` or freeze a developer environment. Keep the lockfile in Git; credentials and private index authentication belong outside project files. Helm and independent SentinelShop container dependencies remain separately managed.

## Run the SentinelShop lab

Choose the deployment guide for your environment:

- [Docker Compose](app/README.md): run the shop locally and practice dependency outages.
- [Kubernetes manifests](k8s/README.md): deploy read-only investigation targets and the crash-demo Pod.
- [Helm chart](helm/sentinelshop/README.md): deploy the shop with configurable images, resources, and replicas.

The documented Kubernetes lab uses context `kind-sentinelops` and namespace `aiops`. Check your current context before deploying or investigating. Metrics may be unavailable without Metrics Server; collectors preserve other evidence and report the collection error.
