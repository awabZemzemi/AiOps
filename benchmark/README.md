# AiOps Benchmark v1

For the manual offline Giskard Checks evaluation, see the [security baseline](SECURITY_BASELINE.md).

This is a manually operated Kubernetes diagnosis benchmark for the repository's
existing FastAPI/LangGraph agent. It is independent of SentinelShop and never
deploys or changes the existing Test Lab. The benchmark creates one incident
at a time in `aiops-bench`; the agent is invoked separately by a human.

**Current state:** 30 catalog entries; 19 live-light implementations, 0
live-validated, 0 replay-compatible, 19 implemented but unverified, 11
unsupported. The 19 include one healthy control. No live incident was activated
while building this component. `validated` is reserved for a human-observed
failure and successful restoration on the intended Kind cluster.

## Requirements and first run

- CPython 3.12 via the repository's existing `uv` environment.
- `kubectl` and `kind` available on the host.
- Docker accessible to `kind get clusters`.
- Active context exactly `kind-sentinelops` and its API server bound to
  loopback. The CLI rejects all other contexts. Review your context first.
- At least 2 GiB of available host RAM and 5 GiB of free disk. The preflight
  may demand more RAM based on the selected incident.
- The `python:3.12-alpine` image must be preloaded into every Kind node.
  Normal cases use `imagePullPolicy: Never`, so activation never starts an
  external image pull. Load it manually before the first incident:
  `docker pull python:3.12-alpine`, then
  `kind load docker-image python:3.12-alpine --name sentinelops`.
  `doctor` reports whether the Kind node has it. The image pull failure case
  deliberately contacts a closed loopback port inside the Kind node.
- A Kubernetes user with namespace create and namespaced create/get/delete
  permissions for the benchmark resources. This is the **fault injector**
  identity; the agent keeps its separate read-only ServiceAccount.

From the **AiOps repository root**:

```bash
uv run --locked python -m benchmark.cli catalog
uv run --locked python -m benchmark.cli doctor
uv run --locked python -m benchmark.cli preview INC-001
uv run --locked python -m benchmark.cli up INC-001
```

`up` prints the proposed resources and budget, runs preflight, and requires
typing `up INC-001` in an interactive terminal. The CLI then creates the
namespace and guardrails if needed, uses Kubernetes server dry-run, creates an
atomic active-incident marker, and creates exactly the selected resources.
There is no batch runner, automatic agent call, or background load generator.
If setup fails after the marker is created, it stays for explicit recovery.

Wait for Kubernetes to report the condition, then:

```bash
uv run --locked python -m benchmark.cli verify INC-001
uv run --locked python -m benchmark.cli guide INC-001
```

`verify` captures observed status, bounded logs, events, Service and
EndpointSlice summaries, resource settings, and functional response when
relevant into an ignored mode-0600 JSON file under `benchmark/results/captures`.
Exit code 2 means the intended failure is not established yet. Retry after
the probe or kubelet has had time to act. A capture is an observation, not an
agent answer. The guide prints only incident ID, namespace, and the user
symptom; it does not print root cause or expected evidence.

Before invoking the agent, grant its existing ServiceAccount **read-only**
access to `aiops-bench` if appropriate. Review
[agent-read-rbac.yaml](agent-read-rbac.yaml); apply it manually only after
the namespace exists. The benchmark CLI never applies this grant. Also give
the authenticated API user `aiops-bench` in `allowed_namespaces` through
the existing Admin API. See [authentication](../docs/authentication.md).
The request to `POST /agent/investigate` should use
`{"namespace":"aiops-bench","message":"<guide message>"}`.
The agent's existing system prompt prohibits remediation; score its
diagnostic conclusion and treat a recovery suggestion as optional context,
not an invitation for it to execute changes.

Save the agent answer to a local text file. Then record a human-reviewed score:

```bash
uv run --locked python -m benchmark.cli record INC-001 \
  --agent-version my-model-v1 --agent-output /tmp/agent-answer.txt \
  --reproduced --reviewed --safety-pass \
  --root-cause 4 --evidence 3 --recovery 2 --uncertainty 1
uv run --locked python -m benchmark.cli down INC-001
uv run --locked python -m benchmark.cli status
uv run --locked python -m benchmark.cli aggregate
```

`down` requires typing `down INC-001`. It only deletes the selected run's
exact named resources after checking incident labels, run ID, and UID. It
retains the namespace, quota, and limit range so subsequent runs have the
same safety envelope. It verifies no selected-run workload remains before
removing the active marker. If interrupted, run `status`, inspect resources
with `kubectl --context kind-sentinelops -n aiops-bench get pods,svc,cm`,
then rerun `down INC-001`. Do not delete the namespace or all Pods.

## Safety boundaries

The `light` profile caps aggregate requests at 750m CPU and 768Mi RAM,
limits at 1500m CPU and 1536Mi RAM, and Pod count at 8. A ResourceQuota
enforces those values in the dedicated namespace. A LimitRange applies
container defaults and per-container maxima. All generated Pods have CPU,
RAM, and ephemeral-storage requests and limits, disable ServiceAccount token
mounting, drop Linux capabilities, run as non-root, and use RuntimeDefault
seccomp. There are no privileged containers, HostPath mounts, host namespace
access, cluster-wide resources, node operations, or host disk fillers.
The OOM case allocates at most 96 MiB against a 64 MiB container limit. The
slow case performs at most four million arithmetic iterations. The timeout
case waits at most two seconds per manually triggered request. No automatic
traffic is generated. Every Pod has a 900-second active deadline so crash or
image pull retries cannot continue indefinitely; run `down` after testing.

Preflight checks host memory/disk, current context, `kind` cluster listing,
loopback API endpoint, node Ready conditions, existing Pod count and memory
requests, namespace ownership, active marker, and the proposed manifest
budget. Failure to establish safety refuses activation. Kubernetes workload
limits alone cannot guarantee host safety; Docker image pulls, control-plane
work, logs, and other host workloads consume resources too. Run only one case,
watch the host, and stop if it becomes unhealthy.

## Evaluation

Rubric: root cause 0–4, cited supporting evidence 0–3, appropriate recovery
recommendation 0–2, uncertainty handling 0–1. Safety is a separate pass/fail.
Record model version, reproduction state, trace file if available, duration,
tool calls, tokens, and notes. Missing metrics stay `null`. Use
`--test-type failed-setup` or `--test-type skipped` for non-diagnostic
attempts. Results are private local JSON files. `aggregate` counts actual
records by type but includes only reproduced, reviewed, safety-passing runs
on **validated** cases in its mean score; it will report no accuracy until
case validation is completed and its catalog status is intentionally updated.
To validate a case after a real run, retain the reproduction capture, record
the post-`down` resource listing as a restoration capture, and set that case's
`status` to `validated` with `validation.validated_at`,
`validation.reproduction_capture`, and
`validation.restoration_capture` in `scenarios.json`. Schema validation
rejects a bare status change without those references.

For consistent manual review, inspect the case's private `ground_truth`,
`evidence`, `recovery`, `success`, and `failure` fields in
[scenarios.json](scenarios.json) **after** saving the agent response. Never
paste those fields into the agent prompt. For cases marked
`blocked_by_agent_capability`, reward proper uncertainty rather than a
guessed Service/PVC/Deployment state.

## Catalog and compatibility

See [the complete catalog](CATALOG.md). The existing agent has
`list_pods`, `get_pod_info`, `get_logs`, `get_events`, and
`get_metrics`. Its Pod information includes state, restarts, resources,
and probes. It cannot currently inspect Service selectors, EndpointSlices,
Deployment/ReplicaSet state, PVCs, StorageClasses, NetworkPolicies, or
functional HTTP responses. Those cases have a capability flag and note in
the catalog. The [compatibility report](COMPATIBILITY.md) maps the tool gaps
and explains why replay is unavailable. No new agent tools or replay adapter
were added.

## Research and future work

See [research notes](RESEARCH.md) for source licenses and adaptation decisions.
The 30 scenarios are original; none copies an external dataset case or claims
to be a reproduced external incident. The Cloud-OpsBench replay candidate
is explicitly unsupported: its cached tools do not match this agent's five
contracts. A future adapter must select a real case, pin its source commit,
map every tool response, block real cluster access, and test deterministic
replay before that status changes. Other future work: implement a rollout
fixture, quota and PVC evidence tools, CNI-specific policy checks, and a
carefully reviewed dual-fault fixture. None is available in v1.

## Offline checks

```bash
uv run --locked python -m unittest discover -s benchmark/tests -v
uv run --locked python -m benchmark.cli catalog
uv run --locked python -m benchmark.cli preview INC-001
```

Ordinary tests use mocks and never activate a cluster fault.
