"""JSON report shape and explicit, allowlisted Kubernetes model conversion."""
from datetime import datetime
from typing import TypedDict

MAX_TEXT_CHARS = 4096


class EvidenceReport(TypedDict):
    timestamp: str
    target: dict
    pod: dict
    containers: list[dict]
    logs: dict
    events: list[dict]
    metrics: dict | None
    collection_errors: list[dict]


def timestamp(value):
    return value.isoformat() if isinstance(value, datetime) else value


def text(value):
    return value[:MAX_TEXT_CHARS] if value is not None else None


def state_info(state):
    if state is None:
        return None
    if state.waiting is not None:
        return {"state": "Waiting", "reason": text(state.waiting.reason),
                "message": text(state.waiting.message)}
    if state.running is not None:
        return {"state": "Running", "started_at": timestamp(state.running.started_at)}
    if state.terminated is not None:
        terminated = state.terminated
        return {"state": "Terminated", "reason": text(terminated.reason),
                "message": text(terminated.message), "exit_code": terminated.exit_code,
                "signal": terminated.signal, "started_at": timestamp(terminated.started_at),
                "finished_at": timestamp(terminated.finished_at)}
    return None


def probe_value(value):
    """Convert a probe model using Kubernetes wire names, without creating a client."""
    if isinstance(value, list):
        return [probe_value(item) for item in value]
    if isinstance(value, dict):
        return {key: probe_value(item) for key, item in value.items()}
    if hasattr(value, "attribute_map"):
        return {wire: probe_value(getattr(value, field))
                for field, wire in value.attribute_map.items() if getattr(value, field) is not None}
    return timestamp(value)


def probe_info(probe):
    if probe is None:
        return None
    result = probe_value(probe)
    # Probe headers and executable arguments can embed credentials.
    http = result.get("httpGet", {})
    for header in http.get("httpHeaders", []):
        header["value"] = "[REDACTED]"
    command = result.get("exec", {}).get("command")
    if command:
        result["exec"]["command"] = ["[REDACTED]"] * len(command)
    return result


def pod_info(pod):
    metadata, spec, status = pod.metadata, pod.spec, pod.status
    return {
        "name": metadata.name, "namespace": metadata.namespace, "uid": metadata.uid,
        "phase": status.phase if status else None,
        "conditions": [{"type": c.type, "status": c.status, "reason": text(c.reason),
                        "message": text(c.message),
                        "last_probe_time": timestamp(c.last_probe_time),
                        "last_transition_time": timestamp(c.last_transition_time)}
                       for c in (status.conditions or [])] if status else [],
        "node_name": spec.node_name, "pod_ip": status.pod_ip if status else None,
        "creation_timestamp": timestamp(metadata.creation_timestamp),
        "owner_references": [{"api_version": o.api_version, "kind": o.kind,
                              "name": o.name, "uid": o.uid, "controller": o.controller}
                             for o in metadata.owner_references or []],
        "restart_policy": spec.restart_policy,
        "service_account_name": spec.service_account_name,
        # Only expose volume names and types, never source configuration or contents.
        "volumes": [{"name": v.name, "types": [wire_name for field, wire_name
                     in v.attribute_map.items() if field != "name" and getattr(v, field) is not None]}
                    for v in spec.volumes or []],
    }


def container_info(container, status, container_type):
    resources = container.resources
    return {
        "name": container.name, "type": container_type, "image": container.image,
        "state": state_info(status.state) if status else None,
        "ready": status.ready if status else None,
        "restart_count": status.restart_count if status else None,
        "previous_termination": state_info(status.last_state)
        if status and status.last_state and status.last_state.terminated else None,
        "resources": {
            key: {resource: value for resource, value in
                  (getattr(resources, key) or {}).items() if resource in ("cpu", "memory")}
            if resources else {} for key in ("requests", "limits")
        },
        "probes": {name: probe_info(getattr(container, name, None)) for name in
                   ("readiness_probe", "liveness_probe", "startup_probe")},
        "environment_variable_names": [env.name for env in container.env or []],
    }


def event_info(event):
    series = event.series
    return {
        "type": event.type, "reason": text(event.reason), "message": text(event.message),
        "count": series.count if series and series.count is not None else event.count,
        "first_observed_at": timestamp(event.first_timestamp or event.event_time),
        "last_observed_at": timestamp(
            (series.last_observed_time if series else None) or event.last_timestamp or event.event_time
        ),
    }
