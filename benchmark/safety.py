"""Fail-closed host, cluster, and manifest preflight."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .manifests import NS, validate_manifest

CONTEXT = "kind-sentinelops"
BUDGET = {"pods": 8, "cpu_request_m": 750, "cpu_limit_m": 1500,
          "memory_request_mi": 768, "memory_limit_mi": 1536}


class SafetyError(RuntimeError):
    pass


def run(argv: list[str], *, input_text: str | None = None, timeout: int = 15) -> str:
    try:
        completed = subprocess.run(argv, input=input_text, text=True, capture_output=True,
                                   timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SafetyError(f"Command unavailable/timed out: {argv[0]}") from error
    if completed.returncode:
        raise SafetyError(f"{' '.join(argv[:4])} failed: {completed.stderr.strip()[:500]}")
    return completed.stdout


def kube(*args: str, input_text: str | None = None, timeout: int = 15) -> str:
    return run(["kubectl", "--context", CONTEXT, *args], input_text=input_text, timeout=timeout)


def kube_json(*args: str) -> dict:
    return json.loads(kube(*args, "-o", "json"))


def cpu_m(value: str) -> int:
    return int(value[:-1]) if value.endswith("m") else int(float(value) * 1000)


def memory_mi(value: str) -> int:
    if value.endswith("Gi"):
        return int(float(value[:-2]) * 1024)
    if value.endswith("Mi"):
        return int(float(value[:-2]))
    raise SafetyError(f"Unsupported memory quantity: {value}")


def footprint(docs: list[dict]) -> dict:
    total = {key: 0 for key in BUDGET}
    for doc in docs:
        if doc["kind"] != "Pod":
            continue
        total["pods"] += 1
        for container in doc["spec"]["containers"]:
            resources = container["resources"]
            total["cpu_request_m"] += cpu_m(resources["requests"]["cpu"])
            total["cpu_limit_m"] += cpu_m(resources["limits"]["cpu"])
            total["memory_request_mi"] += memory_mi(resources["requests"]["memory"])
            total["memory_limit_mi"] += memory_mi(resources["limits"]["memory"])
    return total


def check_budget(docs: list[dict], incident: str) -> dict:
    for doc in docs:
        validate_manifest(doc, incident)
    total = footprint(docs)
    exceeded = [key for key, limit in BUDGET.items() if total[key] > limit]
    if exceeded:
        raise SafetyError(f"Resource budget exceeded: {', '.join(exceeded)}")
    return total


def host_checks(proposed: dict) -> dict:
    text = Path("/proc/meminfo").read_text()
    match = re.search(r"^MemAvailable:\s+(\d+) kB$", text, re.MULTILINE)
    if not match:
        raise SafetyError("Cannot establish available host memory")
    available_mi = int(match.group(1)) // 1024
    free_gi = shutil.disk_usage("/").free // (1024 ** 3)
    # Reserve several GiB for the host and existing applications.
    if available_mi < max(2048, proposed["memory_limit_mi"] * 4):
        raise SafetyError(f"Host memory headroom too small: {available_mi} MiB available")
    if free_gi < 5:
        raise SafetyError(f"Host disk headroom too small: {free_gi} GiB free")
    cpu_count = os.cpu_count()
    load_5m = os.getloadavg()[1]
    if not cpu_count or load_5m > cpu_count * 0.85:
        raise SafetyError(f"Host CPU load too high: {load_5m:.2f} over {cpu_count} CPUs")
    return {"available_memory_mi": available_mi, "free_disk_gi": free_gi,
            "cpu_count": cpu_count, "load_5m": round(load_5m, 2)}


def cluster_identity() -> str:
    if run(["kubectl", "config", "current-context"]).strip() != CONTEXT:
        raise SafetyError(f"Active kubectl context must be {CONTEXT}")
    if CONTEXT != "kind-sentinelops" or "sentinelops" not in run(["kind", "get", "clusters"]).splitlines():
        raise SafetyError("Local Kind cluster is not allowlisted or cannot be verified")
    config = kube_json("config", "view", "--minify")
    clusters = config.get("clusters", [])
    server = clusters[0].get("cluster", {}).get("server", "") if len(clusters) == 1 else ""
    if not re.fullmatch(r"https://(?:127\.0\.0\.1|localhost|\[::1\]):\d+", server):
        raise SafetyError("Kubernetes API server is not on loopback")
    kind_config = run(["kind", "get", "kubeconfig", "--name", "sentinelops"])
    kind_server = re.search(r"^\s*server:\s*(\S+)\s*$", kind_config, re.MULTILINE)
    if not kind_server or kind_server.group(1) != server:
        raise SafetyError("Selected context API server does not match the local Kind cluster")
    return server


def cluster_checks(proposed: dict) -> dict:
    server = cluster_identity()
    nodes = kube_json("get", "nodes").get("items", [])
    if not nodes or len(nodes) > 3:
        raise SafetyError("Unexpected Kind node count")
    for node in nodes:
        name = node["metadata"]["name"]
        if not name.startswith("sentinelops-") or not any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in node.get("status", {}).get("conditions", [])
        ):
            raise SafetyError("Unexpected or unready Kubernetes node")
    pods = kube_json("get", "pods", "--all-namespaces").get("items", [])
    active_pods = [p for p in pods if p.get("status", {}).get("phase") not in ("Succeeded", "Failed")]
    if len(active_pods) > 80:
        raise SafetyError("Cluster already has too many active Pods for the light profile")
    alloc_mi = sum(memory_mi(n["status"]["allocatable"]["memory"]) for n in nodes
                   if n["status"]["allocatable"]["memory"].endswith(("Mi", "Gi")))
    # If the node reports Ki (typical), convert it explicitly.
    if not alloc_mi:
        alloc_mi = sum(int(n["status"]["allocatable"]["memory"][:-2]) // 1024 for n in nodes
                       if n["status"]["allocatable"]["memory"].endswith("Ki"))
    requested_mi = 0
    requested_cpu_m = 0
    for item in active_pods:
        for container in item.get("spec", {}).get("containers", []):
            value = container.get("resources", {}).get("requests", {}).get("memory")
            if value:
                requested_mi += int(value[:-2]) // 1024 if value.endswith("Ki") else memory_mi(value)
            cpu_value = container.get("resources", {}).get("requests", {}).get("cpu")
            if cpu_value:
                requested_cpu_m += cpu_m(cpu_value)
    if alloc_mi <= 0 or requested_mi + proposed["memory_request_mi"] > alloc_mi * 0.75:
        raise SafetyError("Kubernetes node memory requests leave insufficient headroom")
    alloc_cpu_m = sum(cpu_m(n["status"]["allocatable"]["cpu"]) for n in nodes)
    if alloc_cpu_m <= 0 or requested_cpu_m + proposed["cpu_request_m"] > alloc_cpu_m * 0.75:
        raise SafetyError("Kubernetes node CPU requests leave insufficient headroom")
    return {"api_server": server, "node_count": len(nodes), "active_pods": len(active_pods),
            "allocatable_memory_mi": alloc_mi, "existing_memory_requests_mi": requested_mi,
            "allocatable_cpu_m": alloc_cpu_m, "existing_cpu_requests_m": requested_cpu_m}


def namespace_state() -> dict | None:
    try:
        return kube_json("get", "namespace", NS)
    except SafetyError as error:
        if "NotFound" in str(error) or "not found" in str(error):
            return None
        raise


def check_namespace_idle() -> None:
    namespace = namespace_state()
    if namespace is None:
        return
    if namespace.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/managed-by") != "aiops-benchmark":
        raise SafetyError("Existing namespace is not benchmark-owned")
    cms = kube_json("get", "configmaps", "-n", NS).get("items", [])
    if any(x["metadata"]["name"] == "active-incident" for x in cms):
        raise SafetyError("Another incident or interrupted setup is active")
    if any(x["metadata"]["name"] != "kube-root-ca.crt" for x in cms):
        raise SafetyError("Benchmark namespace contains an unexpected ConfigMap")
    pods = kube_json("get", "pods", "-n", NS).get("items", [])
    if pods:
        raise SafetyError("Benchmark namespace contains Pods; inspect and clean them first")
    services = kube_json("get", "services", "-n", NS).get("items", [])
    if any(s["metadata"]["name"] != "kubernetes" for s in services):
        raise SafetyError("Benchmark namespace contains a Service")
    other = kube_json("get", "deployments,replicasets,statefulsets,daemonsets,jobs,cronjobs,persistentvolumeclaims,networkpolicies",
                      "-n", NS).get("items", [])
    if other:
        raise SafetyError("Benchmark namespace contains other workloads or policy objects")


def image_ready() -> bool:
    """True only if the pinned tag is already in every local Kind node."""
    nodes = kube_json("get", "nodes").get("items", [])
    if not nodes:
        return False
    for node in nodes:
        name = node["metadata"]["name"]
        if not name.startswith("sentinelops-"):
            return False
        try:
            run(["docker", "exec", name, "crictl", "inspecti", "docker.io/library/python:3.12-alpine"],
                timeout=10)
        except SafetyError:
            return False
    return True


def preflight(docs: list[dict], incident: str) -> dict:
    proposed = check_budget(docs, incident)
    host = host_checks(proposed)
    cluster = cluster_checks(proposed)
    if incident != "INC-002" and not image_ready():
        raise SafetyError("python:3.12-alpine is not preloaded in every Kind node; "
                          "run 'docker pull python:3.12-alpine' and "
                          "'kind load docker-image python:3.12-alpine --name sentinelops' manually")
    check_namespace_idle()
    return {"proposed": proposed, "host": host, "cluster": cluster}
