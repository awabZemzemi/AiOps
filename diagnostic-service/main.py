"""Command line entry point; JSON goes to stdout, errors to stderr."""
import argparse
import json
import sys

from diagnostics import PodEvidenceError, collect_pod_evidence


def main():
    parser = argparse.ArgumentParser(description="Collect read-only Kubernetes Pod evidence")
    parser.add_argument("pod", nargs="?", default="sentinelshop-catalog-84d5859b99-mnghp")
    parser.add_argument("-n", "--namespace", default="aiops")
    parser.add_argument("--tail-lines", type=int, default=100)
    args = parser.parse_args()
    try:
        report = collect_pod_evidence(args.namespace, args.pod, tail_lines=args.tail_lines)
    except (PodEvidenceError, ValueError):
        # Do not print raw Kubernetes exception messages or response bodies.
        print("Pod not found or evidence collection unavailable; check the target and kubeconfig.", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
