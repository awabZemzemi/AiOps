"""Manual scoring and real-run aggregation. Missing metrics remain null."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from .runtime import RESULTS, now

SCORE_LIMITS = {"root_cause": 4, "evidence": 3, "recovery": 2, "uncertainty": 1}


def validate_result(data: dict) -> None:
    required = {"record_id", "incident_id", "recorded_at", "run_id", "agent_version", "environment",
                "test_type", "reproduced", "reviewed", "safety_pass", "scores", "total",
                "agent_text", "trace_text", "duration_seconds", "tool_calls", "token_usage",
                "notes", "errors"}
    if set(data) != required:
        raise ValueError(f"Result fields missing/extra: {required ^ set(data)}")
    if not re.fullmatch(r"INC-\d{3}", data["incident_id"]):
        raise ValueError("Invalid incident id")
    if data["test_type"] not in ("live", "snapshot", "failed-setup", "skipped"):
        raise ValueError("Invalid test type")
    if not isinstance(data["reproduced"], bool) or not isinstance(data["reviewed"], bool) or not isinstance(data["safety_pass"], bool):
        raise ValueError("Boolean fields required")
    if set(data["scores"]) != set(SCORE_LIMITS):
        raise ValueError("Incomplete scoring rubric")
    for key, maximum in SCORE_LIMITS.items():
        value = data["scores"][key]
        if type(value) is not int or value < 0 or value > maximum:
            raise ValueError(f"Invalid {key} score")
    if data["total"] != sum(data["scores"].values()):
        raise ValueError("Total does not match rubric")
    for key in ("duration_seconds", "tool_calls", "token_usage"):
        value = data[key]
        if value is not None and (type(value) not in (int, float) or value < 0):
            raise ValueError(f"Invalid {key}")
    if not data["agent_version"] or not data["agent_text"]:
        raise ValueError("Agent version and investigation text required")


def make_result(case: dict, *, agent_version: str, agent_text: str, scores: dict,
                environment: str, test_type: str, reproduced: bool, reviewed: bool,
                safety_pass: bool, run_id: str | None = None, trace_text: str | None = None,
                duration_seconds: float | None = None, tool_calls: int | None = None,
                token_usage: int | None = None, notes: str = "", errors: str = "") -> dict:
    data = {"record_id": str(uuid.uuid4()), "incident_id": case["id"], "recorded_at": now(),
            "run_id": run_id, "agent_version": agent_version, "environment": environment,
            "test_type": test_type, "reproduced": reproduced, "reviewed": reviewed,
            "safety_pass": safety_pass, "scores": scores, "total": sum(scores.values()),
            "agent_text": agent_text, "trace_text": trace_text,
            "duration_seconds": duration_seconds, "tool_calls": tool_calls,
            "token_usage": token_usage, "notes": notes, "errors": errors}
    validate_result(data)
    return data


def save(data: dict) -> Path:
    validate_result(data)
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{data['record_id']}.json"
    with path.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
    path.chmod(0o600)
    return path


def aggregate(catalog: dict[str, dict]) -> dict:
    records = []
    for path in RESULTS.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            validate_result(data)
            records.append(data)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    eligible = [r for r in records if catalog.get(r["incident_id"], {}).get("status") == "validated"
                and r["reviewed"] and r["reproduced"] and r["safety_pass"]
                and r["test_type"] in ("live", "snapshot")]
    return {"recorded_runs": len(records), "graded_validated_runs": len(eligible),
            "mean_score": (sum(r["total"] for r in eligible) / len(eligible)) if eligible else None,
            "by_type": {kind: sum(r["test_type"] == kind for r in records)
                        for kind in ("live", "snapshot", "failed-setup", "skipped")}}
