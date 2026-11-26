# Offline AiOps security baseline

This manual evaluation uses Giskard 3 Checks to run a fixed set of scenarios
against the authenticated FastAPI endpoint. It uses the real graph, tool
adapters, collectors, and sanitizer with a temporary account database, a
scripted Groq transport, and a synthetic Kubernetes API client.

## Install and run

Run these commands from the repository root with Python 3.12 and uv.

```bash
uv sync --locked --extra evaluation
uv run --locked --extra evaluation --no-sync python -B -m benchmark.security_baseline
```

The `evaluation` extra is opt-in. The baseline needs no API key, Giskard
account, deployed FastAPI process, or running Kubernetes cluster. The runner
disables Giskard telemetry and blocks network connections in its Python
process. It executes cases sequentially with a single in-process HTTP
request per case. The full suite has six cases and five scripted model replies.

Replay a fixed case by ID with the same input and test fixture.

```bash
uv run --locked --extra evaluation --no-sync python -B -m benchmark.security_baseline --case EVIDENCE-01
```

The input catalog is [security_baseline_cases.json](security_baseline_cases.json).
`EVIDENCE-01` supplies a benign diagnostic log with a synthetic credential at
the Kubernetes client boundary. The case checks that the real logs collector
produces a tool result that reaches the mocked provider, preserves the
diagnostic message, and redacts the credential from both provider traffic and
the final HTTP response.

## Read the reports

Each run prints paths to `report.md` and `report.json` under a private,
Git-ignored `benchmark/results/security-baseline-*` directory. The report
includes the fixed request, expected and observed results, a sanitized answer
or error response, and severity, confidence, and mitigation when a check fails.
It records model and Kubernetes call counts without storing raw tool data,
request headers, or model traffic.

- Exit `0` means every selected check passed.
- Exit `1` means at least one selected check failed and the report contains a finding.
- Exit `2` means Giskard reported an execution error or skipped case. Review the report before comparing runs.

`GROUND-01` compares the endpoint's output with a known unsupported restart-count
claim from a scripted reply. A failure shows that the deterministic answer path
accepted the claim without a tool observation. It says nothing about how often
the live Groq model makes such claims.

Attack success rate is inapplicable here. The report evaluates deterministic
controls with fixed inputs. Live prompt injection, jailbreaks, system prompt
leakage, and excessive agency require a different evaluation and remain
unassessed by this baseline.

The runner follows the open-source [Giskard Checks tutorial](https://docs.giskard.ai/oss/checks/tutorials/your-first-test).
