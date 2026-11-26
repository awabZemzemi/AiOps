"""Strict allowlists for the collector dictionaries and the direct evidence report."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from diagnostic_service.models import MAX_TEXT_CHARS
from diagnostic_service.tools.container_logs import MAX_LOG_BYTES
from diagnostic_service.tools.kubernetes_events import MAX_EVENTS
from diagnostic_service.tools.pod_listing import MAX_PODS

MAX_CONTAINERS = 100
DNS_LABEL = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
Label = Annotated[str, StringConstraints(min_length=1, max_length=63, pattern=rf"^{DNS_LABEL}$")]
PodName = Annotated[str, StringConstraints(min_length=1, max_length=253, pattern=rf"^{DNS_LABEL}(?:\.{DNS_LABEL})*$")]
Text = Annotated[str, StringConstraints(max_length=MAX_TEXT_CHARS)]
LogText = Annotated[str, StringConstraints(max_length=MAX_LOG_BYTES)]
Count = Annotated[int, Field(ge=0)]
Quantity = Annotated[str, StringConstraints(
    max_length=64, pattern=r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+|[numkMGTPE]|[KMGTPE]i)?$",
)]
Window = Annotated[str, StringConstraints(max_length=64, pattern=r"^(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|μs|ms|s|m|h))+$")]


def _timestamp(value: str) -> str:
    if "T" not in value or datetime.fromisoformat(value).tzinfo is None:
        raise ValueError("Expected a timestamp with a timezone.")
    return value


Timestamp = Annotated[str, StringConstraints(max_length=64), AfterValidator(_timestamp)]


class EvidenceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)


class CollectionError(EvidenceModel):
    operation: Literal["pods", "logs.current", "logs.previous", "events", "metrics"]
    status: Annotated[int, Field(ge=0, le=599)] | None = None
    error_type: Text = "CollectionError"
    message: Text = "Kubernetes request failed"
    container: Label | None = None


class ToolEvidence(EvidenceModel):
    collection_errors: list[CollectionError] = Field(max_length=2 * MAX_CONTAINERS + 4)


class PodSummary(EvidenceModel):
    name: PodName
    namespace: Label
    phase: Text | None = None
    ready: bool | None = None
    restart_count: Count | None = None


class PodListing(ToolEvidence):
    namespace: Label
    pods: list[PodSummary] = Field(max_length=MAX_PODS)
    truncated: bool


class Condition(EvidenceModel):
    type: Text
    status: Text
    reason: Text | None = None
    message: Text | None = None
    last_probe_time: Timestamp | None = None
    last_transition_time: Timestamp | None = None


class ContainerState(EvidenceModel):
    state: Literal["Waiting", "Running", "Terminated"]
    reason: Text | None = None
    message: Text | None = None
    exit_code: int | None = None
    signal: int | None = None
    started_at: Timestamp | None = None
    finished_at: Timestamp | None = None


class Owner(EvidenceModel):
    api_version: Text | None = None
    kind: Text
    name: PodName | None = None
    uid: Text | None = None
    controller: bool | None = None


class Volume(EvidenceModel):
    name: Label | None = None
    types: list[Text] = Field(max_length=64)


class Pod(EvidenceModel):
    name: PodName
    namespace: Label
    uid: Text | None = None
    phase: Text | None = None
    conditions: list[Condition] = Field(default_factory=list, max_length=100)
    node_name: Text | None = None
    pod_ip: Text | None = None
    creation_timestamp: Timestamp | None = None
    owner_references: list[Owner] = Field(default_factory=list, max_length=100)
    restart_policy: Text | None = None
    service_account_name: Text | None = None
    volumes: list[Volume] = Field(default_factory=list, max_length=100)


class ResourceAmounts(EvidenceModel):
    cpu: Quantity | None = None
    memory: Quantity | None = None


class Resources(EvidenceModel):
    requests: ResourceAmounts
    limits: ResourceAmounts


class ProbeHeader(EvidenceModel):
    name: Text
    value: Text


class HTTPProbe(EvidenceModel):
    port: int | Label
    host: Text | None = None
    path: Text | None = None
    scheme: Text | None = None
    httpHeaders: list[ProbeHeader] = Field(default_factory=list, max_length=32)


class TCPProbe(EvidenceModel):
    port: int | Label
    host: Text | None = None


class GRPCProbe(EvidenceModel):
    port: int
    service: Text | None = None


class ExecProbe(EvidenceModel):
    command: list[Text] = Field(default_factory=list, max_length=100)


class Probe(EvidenceModel):
    httpGet: HTTPProbe | None = None
    tcpSocket: TCPProbe | None = None
    grpc: GRPCProbe | None = None
    exec: ExecProbe | None = None
    initialDelaySeconds: Count | None = None
    timeoutSeconds: Count | None = None
    periodSeconds: Count | None = None
    successThreshold: Count | None = None
    failureThreshold: Count | None = None
    terminationGracePeriodSeconds: Count | None = None


class Probes(EvidenceModel):
    readiness_probe: Probe | None = None
    liveness_probe: Probe | None = None
    startup_probe: Probe | None = None


class Container(EvidenceModel):
    name: Label
    type: Literal["regular", "init", "ephemeral"] | None = None
    image: Text | None = None
    state: ContainerState | None = None
    ready: bool | None = None
    restart_count: Count | None = None
    previous_termination: ContainerState | None = None
    resources: Resources | None = None
    probes: Probes | None = None
    environment_variable_names: list[Text] = Field(default_factory=list, max_length=256)


class PodInformation(ToolEvidence):
    pod: Pod
    containers: list[Container] = Field(max_length=MAX_CONTAINERS)


class Streams(EvidenceModel):
    current: LogText | None
    previous: LogText | None


LogStreams = Annotated[dict[Label, Streams], Field(max_length=MAX_CONTAINERS)]


class ContainerLogs(ToolEvidence):
    logs: LogStreams


class Event(EvidenceModel):
    type: Text | None = None
    reason: Text | None = None
    message: Text | None = None
    count: Count | None = None
    first_observed_at: Timestamp | None = None
    last_observed_at: Timestamp | None = None


class Events(ToolEvidence):
    events: list[Event] = Field(max_length=MAX_EVENTS)


class ContainerMetrics(ResourceAmounts):
    name: Label


class Metrics(EvidenceModel):
    timestamp: Timestamp | None
    window: Window | None
    containers: list[ContainerMetrics] = Field(max_length=MAX_CONTAINERS)


class ResourceMetrics(ToolEvidence):
    metrics: Metrics | None


class Target(EvidenceModel):
    namespace: Label
    pod: PodName


class PodReport(PodInformation, ContainerLogs, Events, ResourceMetrics):
    timestamp: Timestamp
    target: Target
