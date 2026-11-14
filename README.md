# AiOps

A local Kubernetes/SRE learning project: SentinelShop provides healthy and intentionally failing workloads, `diagnostic-service` collects read-only Pod evidence, and the Sprint 3 LangGraph agent selects diagnostic tools and explains the evidence.

## Current architecture

```text
HTTP client → backend.main:app
                ├── GET /health → process health
                ├── GET /api/v1/pods/{namespace}/{pod_name}/evidence → evidence collector
                └── POST /agent/investigate → agent ⇄ ToolNode → diagnostic collectors

Python/CLI callers → evidence collector → diagnostic collectors → Kubernetes API
```

The collector returns Pod/container details, bounded current/previous logs, related events, optional resource metrics, and collection errors. Pod discovery is also available independently. The agent uses these same collectors through LangChain adapters. All diagnostic operations are read-only.

| Directory | Purpose |
| --- | --- |
| `agent/` | Message state, model configuration and prompt, graph, and LangChain tool adapters. |
| `backend/` | One FastAPI app serving diagnostics and agent investigations, with graph startup lifecycle. |
| `app/` | SentinelShop microservices and Docker Compose lab. |
| `k8s/` | Raw Kubernetes manifests, including the intentional crash-demo Pod. |
| `helm/sentinelshop/` | Configurable Helm deployment for the shop. |
| `diagnostic-service/` | Evidence collectors, Pod discovery, diagnostic API router, CLI, tests, and detailed documentation. |

## Sprint 3 agent

`AgentState` contains only `messages`, using LangGraph's `add_messages` reducer. Namespace, exact Pod name, and container name are explicit tool arguments drawn from the conversation. Tool results retain their namespace/Pod identity in the message history, so one investigation can inspect multiple targets without a shared target field.

The graph keeps the existing `START → agent → tools → agent` loop and ends when the model returns no tool calls. The agent node is a small function inside `create_graph`; `ToolNode` executes tools and `tools_condition` handles routing. The same `get_tools()` list binds the model and configures ToolNode.

```python
from agent.graph import create_graph

graph = create_graph()
result = graph.invoke(
    {"messages": [("user", "Check Pod crash-demo in namespace aiops.")]},
    config={"recursion_limit": 24},
)
print(result["messages"][-1].content)
```

The default `recursion_limit` is 16 graph steps. Both agent and tool executions count toward it; it is not a count of model turns. An unfinished run raises `langgraph.errors.GraphRecursionError`. Callers can catch this exception, as `test_graph.py` does, and streaming callers retain observations already received. The graph does not fabricate cancellation observations or a model answer when the limit is reached.

Set `GROQ_API_KEY` in the repository-root `.env` file or export it in your shell before starting the backend. The backend loads this file at startup, preserving existing environment variables. Manual model/graph scripts still require the key in the environment. `.env.example` lists the required variable. The current profile remains `gpt_oss_120b` on Groq, configured in `agent/factory/model.py`.

The five tools remain `get_pod_info`, `list_pods`, `get_logs`, `get_events`, and `get_metrics`. `ToolRuntime` is used only for the tool-call ID, which is hidden from the model's input schema. Tools return `ToolMessage` observations; they do not write graph state through `Command`. The prompt directs the model to verify Pod existence with tool evidence and obtain container names before reading logs. Verification follows conversation evidence rather than an enforced stored-target gate. Namespace and Pod arguments are still validated by the existing collectors.

The review removed the static `LLMFactory` class in favor of `create_model`, the `AgentNode` class and manual step/cancellation bookkeeping, the ToolNode factory, duplicate edge routing, the separate tool registry module, and the one-prompt directory. `test_tools_node.py` duplicated the ToolNode registration checks in `test_tool_registry.py` and was removed. The unused OpenAI provider dependency and unused `AIOPS_MODEL_PROFILE` example variable were also removed. Existing callers should use `agent.factory.create_model`; `create_graph` no longer takes `max_steps`, and execution limits belong in invocation configuration.

The tool adapter package stays because each diagnostic has its own inputs, collection behavior, and evidence handling. `_common.py` shares error classification and message formatting across all five adapters. Existing collection bounds, safe error messages, identity checks, partial evidence, and metrics limits remain. New Kubernetes diagnostics can add adapters and register them in `get_tools()` without changing state or adding resource-specific nodes.

The diagnostic collectors, Kubernetes/Helm deployments, and SentinelShop runtime code remain unchanged. The diagnostic HTTP module now exposes an `APIRouter` included in `backend.main:app`; there is one FastAPI application and one server. Similar collector/adapter functions serve different interfaces and reuse the same Kubernetes implementation. The service import-path setup is centralized in `agent/tools/__init__.py`; repackaging the collectors would expand this refactor unnecessarily. Repeated logging and HTTP helper code in SentinelShop remains local to independently built container contexts; sharing it would require a separate packaging/build change. Raw manifests and Helm templates provide two documented deployment methods. The original `diagnostic-service/test_connection.py` remains a documented manual experiment and is excluded from automated discovery.

## Run the unified backend

Install the root requirements and set `GROQ_API_KEY` in the repository-root `.env` file (or export it in your shell), then run from the repository root:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn backend.main:app \
  --host 127.0.0.1 --port 8005 --no-access-log
```

The FastAPI `lifespan` handler loads the repository-root `.env` using a path relative to `backend/main.py`, then builds the graph once at startup and stores it on `app.state.agent`. Exported variables take precedence over `.env` values, and file loading does not depend on the process's working directory. The module exposes `app = FastAPI(lifespan=lifespan)`, and importing it does not build the graph. Each running app reuses its graph across requests; compilation is not repeated by the endpoint. Missing model configuration fails startup. Port 8005 avoids the shop's local ports and serves all endpoints below; a second diagnostic server is not needed.

| Method and path | Behavior |
| --- | --- |
| `GET /health` | Returns process health without calling the model or Kubernetes. |
| `GET /api/v1/pods/{namespace}/{pod_name}/evidence` | Returns the existing evidence report; optional `tail_lines` query parameter. |
| `POST /agent/investigate` | Runs an agent investigation from a JSON `message` and returns its final `answer`. |

```bash
curl -X POST http://127.0.0.1:8005/agent/investigate \
  -H 'Content-Type: application/json' \
  -d '{"message":"Check Pod crash-demo in namespace aiops."}'
```

The JSON response contains `answer`, the final agent message's text. The async endpoint awaits `graph.ainvoke()` with a fresh `{"messages": [HumanMessage(...)]}` state. Requests are independent; include the namespace and resource details in the message. Tool calls and intermediate evidence stay within the graph. The existing default recursion limit of 16 graph steps applies.

Empty or whitespace-only messages, missing messages, and non-string messages return 422. Reaching the graph limit returns 503 with an incomplete-evidence explanation. Other investigation failures return a generic 500 without raw exception details. Interactive API documentation is at http://127.0.0.1:8005/docs.

## Project structure

Generated environments, caches, and logs are omitted. Existing application and deployment subtrees are summarized.

```text
AiOps/
├── agent/
│   ├── __init__.py
│   ├── state.py
│   ├── factory/
│   │   ├── __init__.py
│   │   └── model.py
│   ├── prompts.py
│   ├── graph.py
│   └── tools/
│       ├── __init__.py          # Exports and tool registration
│       ├── _common.py          # Safe observations and errors
│       ├── pod_info.py
│       ├── pod_listing.py
│       ├── logs.py
│       ├── events.py
│       └── metrics.py
├── backend/
│   ├── __init__.py
│   └── main.py                 # Unified app, graph lifespan, agent endpoint, diagnostic router
├── diagnostic-service/
│   ├── api.py                  # Diagnostic APIRouter; no separate FastAPI app
│   ├── main.py
│   ├── diagnostics.py
│   ├── kubernetes_client.py
│   ├── evidence_common.py
│   ├── models.py
│   ├── tools/                  # Five reusable Kubernetes collectors
│   ├── tests/                  # Existing offline and opt-in cluster tests
│   ├── test_connection.py
│   └── README.md
├── app/
│   ├── catalog/
│   ├── checkout/
│   ├── inventory/
│   ├── orders/
│   ├── frontend/
│   ├── tests/test_lab.py        # Docker integration script
│   ├── docker-compose.yml
│   └── README.md
├── helm/sentinelshop/           # Chart, templates, values, tests, README
├── k8s/                         # Raw manifests and README
├── test_agent_workflow.py       # Offline graph/tool regression tests
├── test_backend.py              # Offline HTTP and async graph integration tests
├── test_agent.py                # Manual model-only inspection
├── test_graph.py                # Manual Groq + Kubernetes graph run
├── test_pod_listing.py          # Manual Kubernetes discovery inspection
├── test_tool_registry.py        # Local schema, binding, ToolNode checks
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── .gitignore
└── README.md
```

## Validate the agent

From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest test_agent_workflow test_backend -v
.venv/bin/python -m unittest discover -s diagnostic-service/tests -v
.venv/bin/python -m unittest discover -s helm/sentinelshop/tests -v
```

Agent regression tests use a scripted model and mocked collectors with the real graph, reducer, tools, and ToolNode. They check all five tools, message history, parallel targets, streaming, recursion limits and overrides, model binding/configuration, invalid arguments, identity mismatches, safe errors, partial evidence, and metrics bounds. No model API request or cluster access is needed.

Backend tests check `.env` loading before graph construction, environment-variable precedence, graph construction during lifespan startup, deferred construction before startup, reuse across independent requests, awaited async invocation, response text, request validation, error responses, all three endpoints sharing one app/OpenAPI schema, and the HTTP endpoint running a real async graph/ToolNode cycle with a mocked model and collector. The existing diagnostic API tests target the same unified backend.

`test_tool_registry.py` checks schemas and local model binding with `GROQ_API_KEY` set, without an API request. `test_agent.py` makes a model request; `test_graph.py` makes model and read-only Kubernetes requests; `test_pod_listing.py` reads Kubernetes. Run those scripts explicitly when their external services are available. The Docker lab integration script changes stock and restarts containers; see `app/README.md` for its separate setup.

## Collect evidence through the backend

Start `backend.main:app` with the command above. Diagnostic routes use the existing collectors directly in the same process.

The lab uses context `kind-sentinelops` and namespace `aiops`. The API uses the server process's current kubeconfig identity. Keep it bound to loopback: it has no user authentication. Do not share evidence reports without reviewing their application-controlled text for sensitive content.

```bash
curl -i http://127.0.0.1:8005/health
curl -i 'http://127.0.0.1:8005/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20'
.venv/bin/python -m unittest discover -s diagnostic-service/tests -v
```

Swagger UI at http://127.0.0.1:8005/docs shows both diagnostic and agent endpoints. `/health` checks the process; evidence requests return the existing report, including partial collection errors. Invalid inputs return 422; a nonexistent target returns 404. See the [diagnostic-service README](diagnostic-service/README.md) for the full endpoint/error contract, architecture, security decisions, standalone tool examples, and live testing commands. The previous `api:app --app-dir diagnostic-service` entry point is replaced by `backend.main:app`.

## Technical limitations

This is a local development API with no user authentication or in-cluster credential setup. Collection uses sequential reads rather than an atomic snapshot, and per-request Kubernetes timeouts do not impose an overall collection deadline. The service README documents resource bounds, sensitive-text handling, and the known TestClient transport deprecation warning.

Further lab documentation: [application](app/README.md), [Kubernetes](k8s/README.md), [Helm chart](helm/sentinelshop/README.md).
