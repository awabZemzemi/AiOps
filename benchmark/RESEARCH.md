# Research and provenance

Reviewed 2026-11-23. The benchmark code and scenarios are original; no
external source code, manifests, snapshots, or case text was copied. Source
branches below were studied conceptually, so no upstream commit is claimed as
a pinned data dependency. A future imported case must record the exact commit,
case path, and attribution before distribution or execution.

| Source | License | Relevant design evidence | Decision |
| --- | --- | --- | --- |
| [Cloud-OpsBench](https://github.com/LLM4Ops/Cloud-OpsBench) | [MIT](https://github.com/LLM4Ops/Cloud-OpsBench/blob/main/LICENSE) | `metadata.json`, `tool_cache.json`, `raw_data/k8s_states.json`, `logs.json`, `process-label/milestone.json` separate prompt, evidence, ground truth and process evaluation. Its published catalog reports 754 cases and 57 fault types. | Use the separation and evidence-milestone ideas. No case imported; its cached tool interface differs from the current agent. |
| [Coroot RCA Lab](https://github.com/coroot/rca-lab) | [Apache-2.0](https://github.com/coroot/rca-lab/blob/main/LICENSE) | Live application dependencies, meaningful symptoms, and durable recovery. Full lab requires roughly 8 CPU and 16 GiB and includes operators/databases/Kafka. | Use a tiny original HTTP workload. Do not deploy the lab, copy its scenarios, or run its operator. |
| [SREGym](https://github.com/SREGym/SREGym) | [MIT](https://github.com/SREGym/SREGym/blob/main/LICENSE) | Live incident definitions, ground truth, and outcome evaluation. | Use explicit case state and manual rubric; do not install its infrastructure. |

The current repository's five diagnostic tools are the primary compatibility
constraint. Cloud-OpsBench's tool caches include resource descriptions,
application YAML, and dependency tools that have no direct contract-equivalent
in this agent. INC-030 is a research candidate, not a functional replay case.
We did not download a full dataset, execute external scripts, or create a
synthetic replay presented as genuine evidence.

The live cases follow official Kubernetes mechanisms:

- [ResourceQuota](https://kubernetes.io/docs/concepts/policy/resource-quotas/)
  accounts aggregate Pod requests/limits; a
  [LimitRange](https://kubernetes.io/docs/concepts/policy/limit-range/) applies
  per-container defaults and bounds.
- [Liveness and readiness probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
  differ: liveness failures restart a container; readiness failures withhold
  traffic.
- [Node selector and required affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
  constrain scheduling.
- [Secrets](https://kubernetes.io/docs/concepts/configuration/secret/) absent
  at startup produce Pod events; [EndpointSlices](https://kubernetes.io/docs/concepts/services-networking/endpoint-slices/)
  represent Service backends.

No case is called a documented real-world incident. External incidents were
used for design comparison only. Some useful mechanisms (database locks, node
contention, DNS packet delay) require privileged components, large datasets,
or telemetry unavailable here and remain outside the light profile.
