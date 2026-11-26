"""Offline collector/API tests and an explicitly opt-in cluster test."""


def evidence_report(namespace="aiops", pod_name="demo"):
    return {
        "timestamp": "2026-10-20T10:00:00+00:00",
        "target": {"namespace": namespace, "pod": pod_name},
        "pod": {"name": pod_name, "namespace": namespace, "phase": "Running"},
        "containers": [{"name": "app", "state": {"state": "Running"}}],
        "logs": {"app": {"current": "2026-10-20T10:00:00Z started\n", "previous": None}},
        "events": [], "metrics": {"timestamp": "2026-10-20T10:00:00Z", "window": "15s", "containers": []},
        "collection_errors": [],
    }
