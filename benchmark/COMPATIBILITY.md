# Agent and replay compatibility

The repository's agent binds exactly five read-only tools:
`list_pods`, `get_pod_info`, `get_logs`, `get_events`, and
`get_metrics`. The target namespace must also pass application authorization
and Kubernetes RBAC. The optional manual Role/RoleBinding file grants the
existing ServiceAccount only those Kubernetes read verbs in `aiops-bench`.

| Evidence question | Current tool | Limit |
| --- | --- | --- |
| Which Pods exist and are they Ready? | `list_pods` | No Deployment desired replicas or Service endpoint state |
| What happened to a container? | `get_pod_info` | Pod state, restarts, probes, and resource settings; no ConfigMap/Secret values |
| What did the process report? | `get_logs` | Bounded Pod logs only |
| What did kubelet/scheduler report? | `get_events` | Pod events, not Deployment/Quota/PVC event queries |
| What is current Pod CPU/RAM usage? | `get_metrics` | Requires metrics-server; no CPU throttling counters or history |
| Does a Service route to a Pod? | None | Add read-only Service and EndpointSlice tools |
| Was a rollout or scale blocked? | None | Add Deployment, ReplicaSet, and quota/event tools |
| Is a PVC bound? | None | Add PVC and StorageClass tools |
| Does the user request succeed? | None | A narrowly scoped, bounded functional HTTP probe would help |

The [Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench) public
snapshot format offers metadata, Kubernetes states, logs, alerts, metrics,
and cached responses for its own tools. Its `GetResources` and
`DescribeResource` can overlap with this agent's Pod discovery and
information, but the schemas and semantics are not equivalent. Its
`GetAppYAML` and dependency questions have no counterpart here.
There is **no selected source case, copied snapshot, or replay adapter** in
v1. Replay-compatible cases: **0**.

A compatible replay implementation needs to pin one upstream commit and case
ID; retain attribution; map exact snapshot fields into all five existing
tool contracts; return deterministic errors for unsupported calls; and block
all real Kubernetes client construction during replay. It must keep upstream
ground truth and expert trajectories out of agent-visible data. Until those
conditions are tested, INC-030 remains unsupported and is excluded from
accuracy reporting.
