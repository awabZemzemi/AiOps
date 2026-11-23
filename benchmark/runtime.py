"""One-at-a-time cluster operations. No operation touches application namespaces."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .manifests import LABEL, NS, OWNER, build, guardrails
from .safety import SafetyError, check_namespace_idle, cluster_identity, kube, kube_json, preflight

RESULTS = Path(__file__).with_name("results")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(doc: dict, *, dry_run: bool = False) -> None:
    args = ["create"]
    if dry_run:
        args.append("--dry-run=server")
    kube(*args, "-f", "-", input_text=json.dumps(doc), timeout=20)


def ensure_namespace() -> None:
    from .safety import namespace_state
    if namespace_state() is None:
        create({"apiVersion": "v1", "kind": "Namespace",
                "metadata": {"name": NS, "labels": {LABEL: OWNER}}})
    for doc in guardrails():
        kind, name = doc["kind"].lower(), doc["metadata"]["name"]
        try:
            existing = kube_json("get", kind, name, "-n", NS)
        except SafetyError as error:
            if "NotFound" not in str(error) and "not found" not in str(error):
                raise
            create(doc)
            continue
        if existing.get("metadata", {}).get("labels", {}).get(LABEL) != OWNER or existing.get("spec") != doc["spec"]:
            raise SafetyError(f"Existing {kind}/{name} is not the expected benchmark guardrail")


def marker() -> dict | None:
    from .safety import namespace_state
    if namespace_state() is None:
        return None
    try:
        return kube_json("get", "configmap", "active-incident", "-n", NS)
    except SafetyError as error:
        if "NotFound" in str(error) or "not found" in str(error):
            return None
        raise


def active() -> dict | None:
    item = marker()
    if not item:
        return None
    data = item.get("data", {})
    return {"incident_id": data.get("incident_id"), "run_id": data.get("run_id"),
            "started_at": data.get("started_at")}


def status_report(catalog: dict[str, dict]) -> dict:
    current = active()
    if not current:
        return {"active": False}
    case = catalog.get(current["incident_id"])
    if not case:
        return {**current, "error": "Marker references unknown incident"}
    try:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(current["started_at"])).total_seconds()
    except (TypeError, ValueError):
        elapsed = None
    pods = {}
    for name in ("api", "worker"):
        item = _pod(name)
        if item and item["metadata"].get("labels", {}).get("aiops-benchmark.io/run-id") == current["run_id"]:
            pods[name] = {"phase": item.get("status", {}).get("phase"),
                          "restarts": sum(s.get("restartCount", 0)
                                          for s in item.get("status", {}).get("containerStatuses", []))}
    from .safety import footprint
    return {**current, "elapsed_seconds": round(elapsed) if elapsed is not None else None,
            "estimated_budget": footprint(build(case)), "pods": pods}


def activate(case: dict) -> dict:
    docs = build(case)
    report = preflight(docs, case["id"])
    ensure_namespace()
    check_namespace_idle()
    # Server dry-run has no effect, and catches admission errors before the marker.
    for doc in docs:
        create(doc, dry_run=True)
    run_id = str(uuid.uuid4())
    for doc in docs:
        doc["metadata"]["labels"]["aiops-benchmark.io/run-id"] = run_id
    lock = {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": "active-incident", "namespace": NS,
                         "labels": {LABEL: OWNER, "aiops-benchmark.io/incident": case["id"].lower(),
                                    "aiops-benchmark.io/run-id": run_id}},
            "data": {"incident_id": case["id"], "run_id": run_id, "started_at": now()}}
    create(lock)  # Atomic API create refuses a concurrent marker.
    try:
        for doc in docs:
            create(doc)
    except Exception as error:
        try:
            cleanup(case)
        except Exception:
            # Marker remains so a human can inspect and retry cleanup.
            raise SafetyError(f"Setup failed and rollback was incomplete; marker {run_id} retained. "
                              f"Run down {case['id']} to recover.") from error
        raise SafetyError(f"Setup failed; owned resources were rolled back: {error}") from error
    return {"run_id": run_id, **report}


def owned_resources(case: dict, run_id: str) -> list[tuple[str, str, str]]:
    expected = {(doc["kind"].lower(), doc["metadata"]["name"]) for doc in build(case)}
    found = []
    for kind, name in expected:
        try:
            item = kube_json("get", kind, name, "-n", NS)
        except SafetyError as error:
            if "NotFound" in str(error) or "not found" in str(error):
                continue
            raise
        md = item.get("metadata", {})
        labels = md.get("labels", {})
        if (labels.get(LABEL) != OWNER or labels.get("aiops-benchmark.io/incident") != case["id"].lower()
                or labels.get("aiops-benchmark.io/run-id") != run_id):
            raise SafetyError(f"Refusing cleanup of unowned {kind}/{name}")
        found.append((kind, name, md["uid"]))
    return found


def cleanup(case: dict) -> dict:
    cluster_identity()
    current = active()
    if not current or current["incident_id"] != case["id"]:
        raise SafetyError("Selected incident is not active")
    run_id = current["run_id"]
    found = owned_resources(case, run_id)
    # Exact named objects only. API deletion has a UID precondition so a
    # same-named replacement cannot be accidentally removed.
    for kind, name, uid in sorted(found, key=lambda x: x[0] == "configmap"):
        delete_exact(kind, name, uid)
    remaining = owned_resources(case, run_id)
    if remaining:
        raise SafetyError(f"Cleanup incomplete: {remaining}")
    lock = marker()
    if not lock or lock.get("data", {}).get("run_id") != run_id:
        raise SafetyError("Active marker changed; refusing marker deletion")
    delete_exact("configmap", "active-incident", lock["metadata"]["uid"])
    restoration = {"incident_id": case["id"], "run_id": run_id,
                   "restored_at": now(), "owned_resources_remaining": [],
                   "active_marker_remaining": marker() is not None}
    if restoration["active_marker_remaining"]:
        raise SafetyError("Active marker remains after cleanup")
    folder = RESULTS / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}-restoration.json"
    path.write_text(json.dumps(restoration, indent=2) + "\n")
    path.chmod(0o600)
    return {"removed": len(found), "run_id": run_id, "restored": True,
            "restoration_capture": str(path)}


def delete_exact(kind: str, name: str, uid: str) -> None:
    """Delete one namespaced object iff its current UID matches."""
    from kubernetes import client, config
    from kubernetes.client.exceptions import ApiException

    config.load_kube_config(context="kind-sentinelops")
    core = client.CoreV1Api()
    methods = {"pod": core.delete_namespaced_pod,
               "service": core.delete_namespaced_service,
               "configmap": core.delete_namespaced_config_map}
    if kind not in methods:
        raise SafetyError(f"Refusing unsupported cleanup kind {kind}")
    try:
        methods[kind](name=name, namespace=NS,
                      body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=uid)),
                      _request_timeout=15)
    except ApiException as error:
        if error.status != 404:
            raise SafetyError(f"UID-preconditioned delete failed for {kind}/{name}: HTTP {error.status}") from error
    for _ in range(30):
        try:
            current = kube_json("get", kind, name, "-n", NS)
        except SafetyError as error:
            if "NotFound" in str(error) or "not found" in str(error):
                return
            raise
        if current["metadata"]["uid"] != uid:
            raise SafetyError(f"{kind}/{name} was replaced during cleanup")
        time.sleep(0.5)
    raise SafetyError(f"Timed out waiting for {kind}/{name} deletion")


def _pod(name: str) -> dict | None:
    try:
        return kube_json("get", "pod", name, "-n", NS)
    except SafetyError as error:
        if "NotFound" in str(error) or "not found" in str(error):
            return None
        raise


PROBE = ("import json,urllib.request,urllib.error,time; t=time.monotonic(); "
         "u='http://'+__import__('sys').argv[1]+':8080/functional'; "
         "\ntry:\n r=urllib.request.urlopen(u,timeout=4); code=r.status; body=r.read(2048).decode()"
         "\nexcept urllib.error.HTTPError as e:\n code=e.code; body=e.read(2048).decode()"
         "\nexcept Exception as e:\n code=0; body=type(e).__name__+': '+str(e)"
         "\nprint(json.dumps({'code':code,'body':body,'elapsed_ms':round((time.monotonic()-t)*1000)}))")


def functional(host: str) -> dict:
    output = kube("exec", "-n", NS, "api", "--", "python", "-c", PROBE, host, timeout=12)
    return json.loads(output.strip().splitlines()[-1])


def observe(case: dict) -> dict:
    current = active()
    if not current or current["incident_id"] != case["id"]:
        raise SafetyError("Selected incident is not active")
    run_id = current["run_id"]
    pods = {name: _pod(name) for name in ("api", "worker")}
    for name, pod in pods.items():
        if pod and pod["metadata"].get("labels", {}).get("aiops-benchmark.io/run-id") != run_id:
            raise SafetyError(f"Pod {name} is not from this run")
    kind = case["verify"]["kind"]
    api = pods["api"]
    statuses = (api or {}).get("status", {}).get("containerStatuses", [])
    state = statuses[0].get("state", {}) if statuses else {}
    last = statuses[0].get("lastState", {}) if statuses else {}
    ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                for c in (api or {}).get("status", {}).get("conditions", []))
    result = {"incident_id": case["id"], "run_id": run_id, "observed_at": now(),
              "reproduced": False, "reason": "Evidence not yet sufficient",
              "pod": {"phase": (api or {}).get("status", {}).get("phase"),
                      "ready": ready, "state": state, "last_state": last,
                      "restarts": statuses[0].get("restartCount") if statuses else None}}
    waiting = state.get("waiting", {}).get("reason", "")
    if kind == "exit" or kind == "env":
        result["reproduced"] = last.get("terminated", {}).get("exitCode") in (42, 1)
    elif kind == "image":
        result["reproduced"] = waiting in ("ErrImagePull", "ImagePullBackOff")
    elif kind in ("secret", "configmap"):
        result["reproduced"] = waiting == "CreateContainerConfigError"
    elif kind == "liveness":
        result["reproduced"] = (statuses[0].get("restartCount", 0) >= 1) if statuses else False
    elif kind == "readiness":
        result["reproduced"] = bool(state.get("running")) and not ready
    elif kind in ("node_selector", "affinity"):
        result["reproduced"] = result["pod"]["phase"] == "Pending" and not (api or {}).get("spec", {}).get("nodeName")
    elif kind == "oom":
        result["reproduced"] = last.get("terminated", {}).get("reason") == "OOMKilled"
    elif kind == "upstream":
        worker = pods["worker"]
        ws = (worker or {}).get("status", {}).get("containerStatuses", [])
        result["worker"] = {"phase": (worker or {}).get("status", {}).get("phase"),
                            "restarts": ws[0].get("restartCount") if ws else None}
        if ready:
            result["functional"] = functional("localhost")
            result["reproduced"] = result["functional"]["code"] == 500 and bool(ws)
    elif kind in ("selector", "target_port", "dns", "readonly", "http500", "slow", "timeout", "healthy"):
        if ready:
            host = "api" if kind in ("selector", "target_port") else "localhost"
            result["functional"] = functional(host)
            code = result["functional"]["code"]
            if kind == "healthy":
                result["reproduced"] = code == 200
            elif kind == "slow":
                result["reproduced"] = code == 200 and result["functional"]["elapsed_ms"] >= 100
            elif kind in ("selector", "target_port"):
                svc = kube_json("get", "service", "api", "-n", NS)
                result["service"] = {"selector": svc["spec"].get("selector"),
                                     "target_port": svc["spec"]["ports"][0].get("targetPort")}
                result["reproduced"] = (
                    code == 0 and
                    (result["service"]["selector"].get("app.kubernetes.io/name") == "other"
                     if kind == "selector" else result["service"]["target_port"] == 8081)
                )
            elif kind == "readonly":
                result["reproduced"] = code == 500 and "OSError" in result["functional"]["body"]
            elif kind == "http500":
                result["reproduced"] = code == 500 and "FileNotFoundError" in result["functional"]["body"]
            elif kind == "dns":
                result["reproduced"] = code == 500 and "URLError" in result["functional"]["body"]
            elif kind == "timeout":
                worker = pods["worker"]
                worker_ready = any(c.get("type") == "Ready" and c.get("status") == "True"
                                   for c in (worker or {}).get("status", {}).get("conditions", []))
                result["reproduced"] = code == 500 and worker_ready and "TimeoutError" in result["functional"]["body"]
            else:
                result["reproduced"] = code == 500
    result["reason"] = "Expected behavior observed" if result["reproduced"] else "Expected behavior not established; inspect evidence and retry"
    return result


def capture(case: dict, observation: dict) -> Path:
    """Store only allowlisted benchmark evidence, never Pod specs or environment values."""
    current = active()
    if not current or current["run_id"] != observation["run_id"]:
        raise SafetyError("Capture requires the active run")
    pods = {}
    logs = {}
    for name in ("api", "worker"):
        item = _pod(name)
        if item:
            status = item.get("status", {})
            pods[name] = {"uid": item["metadata"].get("uid"),
                          "created_at": item["metadata"].get("creationTimestamp"),
                          "phase": status.get("phase"), "conditions": status.get("conditions", []),
                          "container_statuses": status.get("containerStatuses", []),
                          "resources": item["spec"]["containers"][0].get("resources", {})}
            try:
                logs[name] = kube("logs", "-n", NS, name, "--tail=50", "--limit-bytes=4096")
            except SafetyError:
                logs[name] = None
    events = kube_json("get", "events", "-n", NS).get("items", [])
    pod_uids = {p["uid"] for p in pods.values()}
    event_data = [{"reason": e.get("reason"), "message": e.get("message"),
                   "time": e.get("lastTimestamp"), "involved": e.get("involvedObject", {}).get("name")}
                  for e in events if e.get("involvedObject", {}).get("uid") in pod_uids]
    services = {}
    endpoints = {}
    for name in ("api", "worker"):
        try:
            svc = kube_json("get", "service", name, "-n", NS)
        except SafetyError:
            continue
        services[name] = {"selector": svc["spec"].get("selector"), "ports": svc["spec"].get("ports")}
        try:
            slices = kube_json("get", "endpointslices", "-n", NS,
                               "-l", f"kubernetes.io/service-name={name}").get("items", [])
            endpoints[name] = [{"name": s["metadata"]["name"],
                                "ready_addresses": sum(bool(e.get("conditions", {}).get("ready", True))
                                                       for e in s.get("endpoints", []))}
                               for s in slices]
        except SafetyError:
            endpoints[name] = None
    try:
        version = kube_json("version").get("serverVersion", {}).get("gitVersion")
    except SafetyError:
        version = None
    captured_at = now()
    data = {"incident_id": case["id"], "run_id": current["run_id"], "captured_at": captured_at,
            "observation_window": {"from": current["started_at"], "to": captured_at},
            "kubernetes_version": version,
            "workload_revision": {name: p["uid"] for name, p in pods.items()},
            "observation": observation, "pods": pods,
            "logs": logs, "events": event_data[:50], "services": services, "endpoint_slices": endpoints,
            "source": "live kubectl capture; not model output"}
    folder = RESULTS / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{current['run_id']}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)
    return path
