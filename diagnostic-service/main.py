"""Command line entry point; JSON goes to stdout, errors to stderr."""
import argparse
import json
import sys

from diagnostics import PodEvidenceError, collect_pod_evidence


def main():
    namespace = "aiops"
    pod_name = "sentinelshop-catalog-84d5859b99-mnghp"
    tail_lines = 100

    report = collect_pod_evidence(
        namespace,
        pod_name,
        tail_lines=tail_lines
    )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()