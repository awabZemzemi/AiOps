"""Strict scenario catalog; public prompts and private ground truth are separate."""
from __future__ import annotations

import json
import re
from pathlib import Path

CATALOG = Path(__file__).with_name("scenarios.json")
LIVE_KINDS = {
    "exit", "image", "secret", "configmap", "env", "liveness", "readiness",
    "node_selector", "affinity", "oom", "selector", "target_port", "dns",
    "upstream", "readonly", "http500", "slow", "timeout", "healthy",
}
AGENT_TOOLS = {"list_pods", "get_pod_info", "get_logs", "get_events", "get_metrics"}


def validate(case: dict) -> None:
    required = {
        "id", "title", "category", "difficulty", "provenance", "mode",
        "status", "mechanism", "prompt", "ground_truth", "evidence",
        "recovery", "success", "failure", "requirements", "footprint",
        "safety", "setup", "baseline", "fault", "verify", "cleanup",
        "restoration", "timeout_seconds", "failure_handling", "agent_tools",
        "blocked_by_agent_capability", "capability_note",
        "affected_component", "alternatives_to_exclude",
        "validation",
    }
    if set(case) != required:
        raise ValueError(f"{case.get('id')}: fields missing/extra: {required ^ set(case)}")
    if not re.fullmatch(r"INC-\d{3}", case["id"]):
        raise ValueError("bad incident id")
    if case["mode"] not in {"live-light", "snapshot-replay", "optional-dependency", "unsupported"}:
        raise ValueError(f"{case['id']}: bad mode")
    if case["status"] not in {"implemented-unverified", "validated", "unsupported"}:
        raise ValueError(f"{case['id']}: bad status")
    if case["status"] == "validated" and not (
        isinstance(case["validation"], dict)
        and case["validation"].get("reproduction_capture")
        and case["validation"].get("restoration_capture")
        and case["validation"].get("validated_at")
    ):
        raise ValueError(f"{case['id']}: validated case lacks live evidence")
    if case["status"] != "validated" and case["validation"] is not None:
        raise ValueError(f"{case['id']}: unvalidated case claims validation evidence")
    if case["mode"] == "live-light" and case["fault"]["kind"] not in LIVE_KINDS:
        raise ValueError(f"{case['id']}: no live implementation")
    if case["mode"] != "live-light" and case["status"] != "unsupported":
        raise ValueError(f"{case['id']}: unsupported case marked implemented")
    if not set(case["agent_tools"]) <= AGENT_TOOLS:
        raise ValueError(f"{case['id']}: unknown agent tool")
    if case["blocked_by_agent_capability"] and not case["capability_note"]:
        raise ValueError(f"{case['id']}: missing capability explanation")
    if "ground_truth" in case["prompt"] or case["mechanism"].lower() in case["prompt"].lower():
        raise ValueError(f"{case['id']}: possible answer leakage")
    for key in ("mechanism", "prompt", "ground_truth", "recovery", "success", "failure", "cleanup", "restoration"):
        if not isinstance(case[key], str) or not case[key].strip():
            raise ValueError(f"{case['id']}: empty {key}")
    if not case["evidence"] or not case["verify"]:
        raise ValueError(f"{case['id']}: no verification/evidence")
    if not case["affected_component"] or not case["alternatives_to_exclude"]:
        raise ValueError(f"{case['id']}: missing causal evaluation fields")
    if case["mode"] == "live-light" and (not case["fault"].get("change") or not case["verify"].get("expected")):
        raise ValueError(f"{case['id']}: missing concrete fault/verification definition")


def load_catalog() -> dict[str, dict]:
    data = json.loads(CATALOG.read_text())
    if len(data) != 30:
        raise ValueError(f"Expected 30 cases, got {len(data)}")
    result = {}
    for case in data:
        validate(case)
        if case["id"] in result:
            raise ValueError(f"duplicate {case['id']}")
        result[case["id"]] = case
    return result


def public_case(case: dict) -> dict:
    """Only this view may be shown to the diagnostic agent."""
    return {"incident_id": case["id"], "namespace": "aiops-bench", "message": case["prompt"]}


def missing_tools(case: dict, available: set[str] = AGENT_TOOLS) -> list[str]:
    return sorted(set(case["agent_tools"]) - available)
