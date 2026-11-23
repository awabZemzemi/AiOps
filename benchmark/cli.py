"""Explicit, interactive command-line entry point for one incident at a time."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import load_catalog, public_case
from .evaluation import aggregate, make_result, save
from .manifests import build
from .runtime import activate, active, capture, cleanup, observe, status_report
from .safety import BUDGET, SafetyError, check_budget, cluster_checks, host_checks, image_ready, namespace_state, preflight


def confirmation(action: str, case_id: str) -> None:
    if not sys.stdin.isatty():
        raise SafetyError("Mutation requires an interactive terminal")
    expected = f"{action} {case_id}"
    value = input(f"Type '{expected}' to confirm: ")
    if value != expected:
        raise SafetyError("Confirmation did not match; no change made")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m benchmark.cli")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("catalog", "doctor", "status", "aggregate"):
        sub.add_parser(name)
    for name in ("preview", "up", "verify", "guide", "down"):
        sub.add_parser(name).add_argument("incident_id")
    record = sub.add_parser("record")
    record.add_argument("incident_id")
    record.add_argument("--agent-version", required=True)
    record.add_argument("--agent-output", required=True, type=Path)
    record.add_argument("--environment", default="local Kind")
    record.add_argument("--test-type", choices=("live", "snapshot", "failed-setup", "skipped"), default="live")
    record.add_argument("--reproduced", action="store_true")
    record.add_argument("--reviewed", action="store_true")
    record.add_argument("--safety-pass", action="store_true")
    for key, maximum in (("root-cause", 4), ("evidence", 3), ("recovery", 2), ("uncertainty", 1)):
        record.add_argument("--" + key, type=int, choices=range(maximum + 1), required=True)
    record.add_argument("--trace", type=Path)
    record.add_argument("--duration-seconds", type=float)
    record.add_argument("--tool-calls", type=int)
    record.add_argument("--token-usage", type=int)
    record.add_argument("--notes", default="")
    record.add_argument("--errors", default="")
    return p


def print_json(value) -> None:
    print(json.dumps(value, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    cases = load_catalog()
    try:
        if args.command == "catalog":
            print("ID       Mode                 Status                  Agent gap  Title")
            for case in cases.values():
                print(f"{case['id']:<8} {case['mode']:<20} {case['status']:<23} "
                      f"{'yes' if case['blocked_by_agent_capability'] else 'no':<10} {case['title']}")
        elif args.command == "doctor":
            reference_case = cases["INC-001"]
            proposed = check_budget(build(reference_case), reference_case["id"])
            print_json({"host": host_checks(proposed), "cluster": cluster_checks(proposed),
                        "namespace": (namespace_state() or {}).get("metadata", {}).get("name"),
                        "active": active(), "reference_case": "INC-001",
                        "reference_footprint": proposed, "image_preloaded": image_ready(),
                        "light_budget": BUDGET})
        elif args.command == "status":
            print_json(status_report(cases))
        elif args.command == "aggregate":
            print_json(aggregate(cases))
        else:
            case = cases.get(args.incident_id)
            if not case:
                raise SafetyError("Unknown incident id")
            if args.command == "guide":
                print_json(public_case(case))
            elif args.command == "preview":
                docs = build(case) if case["mode"] == "live-light" else []
                print_json({"id": case["id"], "title": case["title"], "mode": case["mode"],
                            "status": case["status"], "mechanism": case["mechanism"],
                            "budget": check_budget(docs, case["id"]) if docs else case["footprint"],
                            "resources": [f"{d['kind']}/{d['metadata']['name']}" for d in docs],
                            "namespace": "aiops-bench", "risk": case["safety"],
                            "agent_gap": case["capability_note"], "setup": case["setup"],
                            "verify": case["verify"], "cleanup": case["cleanup"]})
            elif args.command == "up":
                if case["mode"] != "live-light":
                    raise SafetyError(case["fault"].get("reason", "Not runnable"))
                docs = build(case)
                safety = preflight(docs, case["id"])
                print_json({"id": case["id"], "budget": check_budget(docs, case["id"]),
                            "resources": [f"{d['kind']}/{d['metadata']['name']}" for d in build(case)],
                            "namespace": "aiops-bench", "risk": case["safety"], "preflight": safety})
                confirmation("up", case["id"])
                print_json(activate(case))
            elif args.command == "verify":
                observation = observe(case)
                path = capture(case, observation)
                print_json({**observation, "capture": str(path)})
                if not observation["reproduced"]:
                    return 2
            elif args.command == "down":
                print_json({"selected": case["id"], "active": active(),
                            "cleanup": case["cleanup"]})
                confirmation("down", case["id"])
                print_json(cleanup(case))
            elif args.command == "record":
                agent_text = args.agent_output.read_text()
                if len(agent_text) > 100_000:
                    raise SafetyError("Agent output exceeds 100 KB")
                trace_text = args.trace.read_text() if args.trace else None
                if trace_text and len(trace_text) > 100_000:
                    raise SafetyError("Trace exceeds 100 KB")
                current = active()
                result = make_result(case, agent_version=args.agent_version,
                    agent_text=agent_text, scores={"root_cause": args.root_cause,
                    "evidence": args.evidence, "recovery": args.recovery,
                    "uncertainty": args.uncertainty}, environment=args.environment,
                    test_type=args.test_type, reproduced=args.reproduced, reviewed=args.reviewed,
                    safety_pass=args.safety_pass,
                    run_id=current["run_id"] if current and current["incident_id"] == case["id"] else None,
                    trace_text=trace_text, duration_seconds=args.duration_seconds,
                    tool_calls=args.tool_calls, token_usage=args.token_usage,
                    notes=args.notes, errors=args.errors)
                print_json({"saved": str(save(result)), "total": result["total"]})
    except (SafetyError, ValueError, OSError) as error:
        print(f"Benchmark refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
