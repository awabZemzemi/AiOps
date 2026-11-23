"""Pure manifest construction and static rejection of risky specifications."""
from __future__ import annotations

from pathlib import Path

NS = "aiops-bench"
IMAGE = "python:3.12-alpine"
OWNER = "aiops-benchmark"
LABEL = "app.kubernetes.io/managed-by"


def labels(incident: str) -> dict:
    return {LABEL: OWNER, "aiops-benchmark.io/incident": incident.lower()}


def metadata(name: str, incident: str) -> dict:
    return {"name": name, "namespace": NS, "labels": labels(incident)}


def guardrails() -> list[dict]:
    return [
        {"apiVersion": "v1", "kind": "ResourceQuota",
         "metadata": {"name": "bench-budget", "namespace": NS, "labels": {LABEL: OWNER}},
         "spec": {"hard": {"pods": "8", "requests.cpu": "750m", "limits.cpu": "1500m",
                           "requests.memory": "768Mi", "limits.memory": "1536Mi",
                           "requests.ephemeral-storage": "512Mi", "limits.ephemeral-storage": "1Gi"}}},
        {"apiVersion": "v1", "kind": "LimitRange",
         "metadata": {"name": "bench-limits", "namespace": NS, "labels": {LABEL: OWNER}},
         "spec": {"limits": [{"type": "Container",
                              "min": {"cpu": "10m", "memory": "16Mi"},
                              "max": {"cpu": "500m", "memory": "512Mi"},
                              "defaultRequest": {"cpu": "50m", "memory": "64Mi"},
                              "default": {"cpu": "200m", "memory": "128Mi"}}]}},
    ]


def configmap(incident: str) -> dict:
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata("app-code", incident),
            "data": {"app.py": Path(__file__).with_name("workload.py").read_text()}}


def pod(name: str, incident: str, *, env: list | None = None, command: list | None = None,
        image: str = IMAGE, resources: dict | None = None, readonly: bool = False,
        readiness: str | None = None, liveness: str | None = None, selector: dict | None = None,
        affinity: dict | None = None) -> dict:
    container = {"name": "app", "image": image, "imagePullPolicy": "Never",
                 "command": command or ["python", "-B", "/app/app.py"],
                 "env": env or [],
                 "resources": resources or {"requests": {"cpu": "50m", "memory": "64Mi", "ephemeral-storage": "16Mi"},
                                            "limits": {"cpu": "200m", "memory": "128Mi", "ephemeral-storage": "64Mi"}},
                 "securityContext": {"allowPrivilegeEscalation": False,
                                     "readOnlyRootFilesystem": readonly,
                                     "runAsNonRoot": True, "runAsUser": 10001,
                                     "capabilities": {"drop": ["ALL"]},
                                     "seccompProfile": {"type": "RuntimeDefault"}},
                 "volumeMounts": [{"name": "code", "mountPath": "/app", "readOnly": True}]}
    if readiness:
        container["readinessProbe"] = {"httpGet": {"path": readiness, "port": 8080},
                                       "initialDelaySeconds": 2, "periodSeconds": 3}
    if liveness:
        container["livenessProbe"] = {"httpGet": {"path": liveness, "port": 8080},
                                      "initialDelaySeconds": 3, "periodSeconds": 4, "failureThreshold": 2}
    spec = {"automountServiceAccountToken": False, "restartPolicy": "Always",
            "activeDeadlineSeconds": 900,
            "terminationGracePeriodSeconds": 5,
            "containers": [container], "volumes": [{"name": "code", "configMap": {"name": "app-code"}}]}
    if selector:
        spec["nodeSelector"] = selector
    if affinity:
        spec["affinity"] = affinity
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata(name, incident),
            "spec": spec}


def service(name: str, incident: str, selector: str = "api", port: int = 8080) -> dict:
    return {"apiVersion": "v1", "kind": "Service", "metadata": metadata(name, incident),
            "spec": {"selector": {"app.kubernetes.io/name": selector},
                     "ports": [{"name": "http", "port": 8080, "targetPort": port}]}}


def build(case: dict) -> list[dict]:
    incident = case["id"]
    kind = case["fault"]["kind"]
    if case["mode"] != "live-light":
        raise ValueError("Unsupported scenario cannot build live resources")
    docs = [configmap(incident)]
    app = pod("api", incident, readiness="/ready")
    app["metadata"]["labels"]["app.kubernetes.io/name"] = "api"
    env = app["spec"]["containers"][0]["env"]
    container = app["spec"]["containers"][0]
    if kind == "exit":
        container["command"] = ["python", "-c", "import sys; print('startup failed', flush=True); sys.exit(42)"]
    elif kind == "image":
        # The node's closed loopback port produces a genuine image-pull error
        # without contacting an external registry.
        container["image"] = "127.0.0.1:1/aiops-bench/no-such-image:v1"
        container["imagePullPolicy"] = "Always"
    elif kind == "secret":
        env.append({"name": "REQUIRED_KEY", "valueFrom": {"secretKeyRef": {"name": "app-credentials", "key": "key"}}})
    elif kind == "configmap":
        env.append({"name": "REQUIRED_CONFIG", "valueFrom": {"configMapKeyRef": {"name": "app-settings", "key": "setting"}}})
    elif kind == "env":
        env.append({"name": "PORT", "value": "invalid"})
    elif kind == "liveness":
        container["livenessProbe"] = {"httpGet": {"path": "/status", "port": 8080},
                                      "initialDelaySeconds": 3, "periodSeconds": 4, "failureThreshold": 2}
    elif kind == "readiness":
        container["readinessProbe"]["httpGet"]["path"] = "/status"
    elif kind == "node_selector":
        app["spec"]["nodeSelector"] = {"aiops-benchmark.io/never-present": "required"}
    elif kind == "affinity":
        app["spec"]["affinity"] = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution":
            {"nodeSelectorTerms": [{"matchExpressions": [{"key": "aiops-benchmark.io/never-present",
                                                          "operator": "In", "values": ["required"]}]}]}}}
    elif kind == "oom":
        container["resources"]["limits"]["memory"] = "64Mi"
        container["command"] = ["python", "-c",
            "import time; print('bounded startup allocation',flush=True); a=bytearray(96*1024*1024); time.sleep(2)"]
    elif kind == "selector":
        docs.append(service("api", incident, selector="other"))
    elif kind == "target_port":
        docs.append(service("api", incident, port=8081))
    elif kind == "dns":
        env.append({"name": "DEPENDENCY_URL", "value": "http://missing.aiops-bench.svc.cluster.local:8080/functional"})
    elif kind == "upstream":
        env.append({"name": "DEPENDENCY_URL", "value": "http://worker:8080/functional"})
        worker = pod("worker", incident, command=["python", "-c", "import sys; sys.exit(7)"])
        worker["metadata"]["labels"]["app.kubernetes.io/name"] = "worker"
        docs.extend([worker, service("worker", incident, selector="worker")])
    elif kind == "readonly":
        env.append({"name": "WRITE_PATH", "value": "/tmp/bench-record"})
        container["securityContext"]["readOnlyRootFilesystem"] = True
    elif kind == "http500":
        env.append({"name": "TEMPLATE_FILE", "value": "/app/template.txt"})
    elif kind == "slow":
        env.append({"name": "WORK_ITERATIONS", "value": "4000000"})
    elif kind == "timeout":
        env.extend([{"name": "DEPENDENCY_URL", "value": "http://worker:8080/functional"},
                    {"name": "DEPENDENCY_TIMEOUT", "value": "0.2"}])
        worker = pod("worker", incident, env=[{"name": "UPSTREAM_DELAY", "value": "2"}], readiness="/ready")
        worker["metadata"]["labels"]["app.kubernetes.io/name"] = "worker"
        docs.extend([worker, service("worker", incident, selector="worker")])
    elif kind != "healthy":
        raise ValueError(f"Unknown fault: {kind}")
    docs.append(app)
    return docs


ALLOWED_KINDS = {"ConfigMap", "Pod", "Service", "ResourceQuota", "LimitRange"}
FORBIDDEN_SPEC = {"hostNetwork", "hostPID", "hostIPC", "hostUsers"}


def validate_manifest(doc: dict, incident: str | None = None) -> None:
    if doc.get("apiVersion") != "v1" or doc.get("kind") not in ALLOWED_KINDS:
        raise ValueError("Unsupported Kubernetes kind/API")
    md = doc.get("metadata", {})
    if md.get("namespace") != NS or md.get("labels", {}).get(LABEL) != OWNER:
        raise ValueError("Manifest leaves benchmark ownership boundary")
    if incident and md["labels"].get("aiops-benchmark.io/incident") != incident.lower():
        raise ValueError("Manifest incident ownership mismatch")
    spec = doc.get("spec", {})
    if any(spec.get(field) for field in FORBIDDEN_SPEC):
        raise ValueError("Host namespace access forbidden")
    if doc["kind"] == "Pod":
        if spec.get("automountServiceAccountToken") is not False or len(spec.get("containers", [])) != 1:
            raise ValueError("Pod identity or container count unsafe")
        if not 1 <= spec.get("activeDeadlineSeconds", 0) <= 900:
            raise ValueError("Pod lifetime is not bounded")
        for volume in spec.get("volumes", []):
            if set(volume) - {"name", "configMap"}:
                raise ValueError("Only ConfigMap volumes allowed")
        c = spec["containers"][0]
        if c.get("imagePullPolicy") != "Never" and not (
            incident == "INC-002" and c.get("imagePullPolicy") == "Always"
            and c.get("image", "").startswith("127.0.0.1:1/")
        ):
            raise ValueError("Unexpected image pull behavior")
        sc = c.get("securityContext", {})
        if sc.get("privileged") or sc.get("allowPrivilegeEscalation") is not False or sc.get("runAsNonRoot") is not True:
            raise ValueError("Unsafe container security context")
        if c.get("resources", {}).get("limits", {}).get("ephemeral-storage") != "64Mi":
            raise ValueError("Missing local storage cap")
