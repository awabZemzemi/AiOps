# Incident catalog

All 30 cases are original. `implemented-unverified` means a concrete manifest and
verifier exist but no live Kind reproduction or restoration has been observed.
Cases with an agent gap can be activated where marked live, but the current
five-tool agent cannot prove the complete mechanism. Do not include those
cases in fair accuracy comparisons until the tool gap is resolved.

| ID | Failure mechanism | Difficulty | Source | Mode | Required evidence | Status | Agent gap |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INC-001 | A container process exits 42 immediately; kubelet backs it off. | easy | Original | live-light | Previous termination exit 42; CrashLoopBackOff; BackOff event. | implemented-unverified | No |
| INC-002 | The image reference points to a closed local registry endpoint; kubelet cannot pull it. | easy | Original | live-light | ErrImagePull or ImagePullBackOff; Failed event names image. | implemented-unverified | No |
| INC-003 | A required Secret key reference is absent; kubelet cannot create container config. | easy | Original | live-light | CreateContainerConfigError; Failed event names missing Secret. | implemented-unverified | No |
| INC-004 | A required ConfigMap reference is absent; kubelet cannot form the container environment. | easy | Original | live-light | CreateContainerConfigError; Failed event names ConfigMap. | implemented-unverified | No |
| INC-005 | An invalid port value fails application configuration validation. | easy | Original | live-light | Nonzero previous exit; bounded log states invalid port. | implemented-unverified | No |
| INC-006 | The liveness probe targets a nonexistent path and kubelet restarts the container. | easy | Original | live-light | Unhealthy event and repeated restarts; probe path in Pod info. | implemented-unverified | No |
| INC-007 | The readiness probe targets a nonexistent path; Pod remains unready. | easy | Original | live-light | Running container, Ready false, Unhealthy event and probe path. | implemented-unverified | No |
| INC-008 | A node selector requires a label absent from local nodes. | easy | Original | live-light | Pending Pod; FailedScheduling event mentions node selector. | implemented-unverified | No |
| INC-009 | Required node affinity demands a nonexistent label value. | easy | Original | live-light | Pending Pod; FailedScheduling event cites affinity or selector. | implemented-unverified | No |
| INC-010 | A capped 96 MiB allocation exceeds a 64 MiB container memory limit. | easy | Original | live-light | Last termination OOMKilled; memory limit 64Mi. | implemented-unverified | No |
| INC-011 | Service selector does not match the healthy Pod labels; no usable EndpointSlice addresses. | medium | Original | live-light | Healthy Pod plus Service selector and empty EndpointSlice. | implemented-unverified | Yes |
| INC-012 | Service targetPort points to 8081 while the process listens on 8080. | medium | Original | live-light | Ready Pod, Service targetPort 8081, refused client connection. | implemented-unverified | Yes |
| INC-013 | The configured upstream DNS name has no corresponding Service. | medium | Original | live-light | API log reports DNS resolution failure; no matching Service. | implemented-unverified | Yes |
| INC-014 | The upstream Pod exits; Service has no ready endpoints. | medium | Original | live-light | API upstream error; upstream restart and service endpoint absence. | implemented-unverified | Yes |
| INC-015 | A readOnlyRootFilesystem prevents writing under the expected path. | medium | Original | live-light | API logs permission or read-only filesystem error; Pod remains Ready. | implemented-unverified | Yes |
| INC-016 | A required template file is absent from the mounted configuration. | medium | Original | live-light | Ready Pod; request produces 500 and missing file log. | implemented-unverified | Yes |
| INC-017 | The route performs a bounded inefficient computation set by configuration. | medium | Original | live-light | Ready Pod; functional request duration exceeds threshold; logs show iteration count. | implemented-unverified | Yes |
| INC-018 | Upstream intentionally waits 2 seconds while client timeout is 0.2 seconds. | medium | Original | live-light | Ready Pods; API timeout log and bounded response duration. | implemented-unverified | Yes |
| INC-019 | A new Deployment revision references a missing configuration file. | medium | Original | unsupported | Deployment and ReplicaSet revision plus failing new Pod. | unsupported | Yes |
| INC-020 | ResourceQuota rejects new Pods after a scale request. | medium | Original | unsupported | Deployment replica shortfall and FailedCreate event with quota. | unsupported | Yes |
| INC-021 | A restrictive CPU limit throttles a bounded workload. | medium | Original | unsupported | CPU throttling counters and latency correlation. | unsupported | Yes |
| INC-022 | A namespace NetworkPolicy denies API egress to the dependency. | medium | Original | unsupported | Policy selectors and connection timeout; CNI enforces policy. | unsupported | Yes |
| INC-023 | PVC requests a nonexistent StorageClass. | medium | Original | unsupported | PVC Pending and provisioning events. | unsupported | Yes |
| INC-024 | A database workload is unavailable. | medium | Original | optional-dependency | Dependency logs and connection failures. | unsupported | Yes |
| INC-025 | An application-level configuration defect leaves health route unaffected. | hard | Original | unsupported | Functional error plus config provenance. | unsupported | Yes |
| INC-026 | One fault affects the reported path; a second unrelated fault is present. | hard | Original | unsupported | Causal path evidence distinguishes primary and incidental fault. | unsupported | Yes |
| INC-027 | An unrelated failing workload is noise; actual cause lies on API path. | hard | Original | unsupported | Separate workload and API traces identify causality. | unsupported | Yes |
| INC-028 | All benchmark workload health and functional checks pass. | hard | Original | live-light | Ready Pod and successful functional response. | implemented-unverified | Yes |
| INC-029 | The available snapshot lacks evidence to determine a cause. | hard | Original | unsupported | No decisive event or log; competing hypotheses remain. | unsupported | Yes |
| INC-030 | External case not selected or integrated; no evidence can be served yet. | hard | Original | snapshot-replay | Requires original snapshot and compatible tool cache. | unsupported | Yes |

For each entry, [scenarios.json](scenarios.json) provides the full prompt,
private ground truth, expected evidence, success/failure criteria, setup,
verification, timeout, cleanup, resource estimate, provenance, and exact
unsupported reason where applicable.

## Agent tool map

Current tools: `list_pods`, `get_pod_info` (phase, restart count, probes,
requests/limits), `get_logs`, `get_events`, and `get_metrics` (Pod usage
only when metrics-server exists). The `agent_tools` field in each case lists
the supported tools useful for the case. `blocked_by_agent_capability` marks
cases where those tools cannot establish all of the required evidence. The
recommended additions are read-only Service/EndpointSlice, Deployment/
ReplicaSet, PVC/StorageClass, NetworkPolicy, and functional endpoint tools.
