"""Mint a short-lived diagnostic kubeconfig without exposing developer credentials."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def kubectl_json(context, *args):
    result = subprocess.run(
        ["kubectl", "--context", context, "--request-timeout=15s", *args],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def create(context, output, duration="1h"):
    output = Path(output).expanduser().absolute()
    if output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Store restricted credentials outside the repository.")
    personal = [Path.home() / ".kube/config"]
    personal.extend(Path(p).expanduser() for p in os.environ.get("KUBECONFIG", "").split(os.pathsep) if p)
    if output.is_symlink() or output.resolve() in {p.resolve() for p in personal}:
        raise ValueError("Choose a separate output file; personal kubeconfigs cannot be overwritten.")
    # Flatten CA references but copy only the cluster's public TLS configuration.
    source = kubectl_json(context, "config", "view", "--minify", "--flatten", "--raw", "-o", "json")
    cluster = source["clusters"][0]["cluster"]
    if (not cluster["server"].startswith("https://") or cluster.get("insecure-skip-tls-verify")
            or not cluster.get("certificate-authority-data")):
        raise ValueError("Source cluster must use HTTPS and a CA certificate.")
    tls = {key: cluster[key] for key in ("server", "certificate-authority-data", "tls-server-name") if key in cluster}
    issued = kubectl_json(context, "-n", "aiops", "create", "token", "aiops-agent",
                          "--duration", duration, "-o", "json")
    document = {
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "aiops-cluster", "cluster": tls}],
        "users": [{"name": "aiops-agent", "user": {"token": issued["status"]["token"]}}],
        "contexts": [{"name": "aiops-agent", "context": {
            "cluster": "aiops-cluster", "user": "aiops-agent", "namespace": "aiops"}}],
        "current-context": "aiops-agent",
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(document, stream)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return issued["status"]["expirationTimestamp"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit administrator Kind context")
    parser.add_argument("--output", required=True, help="Separate private kubeconfig path")
    parser.add_argument("--duration", default="1h", help="Requested lifetime; server determines actual expiry")
    args = parser.parse_args()
    try:
        expires = create(args.context, args.output, args.duration)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        print("Credential generation failed; check context, RBAC, TokenRequest access and output path.")
        return 1
    print(f"Restricted kubeconfig written privately. Token expires at {expires}; rerun this command to renew.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
