"""Read-only manual inspection: python test_pod_listing.py.

Edit namespaces below. Uses the existing kubeconfig and current context;
for the lab, select kind-sentinelops before running. No LLM API key is needed.
"""

import json

from langgraph.prebuilt import ToolNode
from rich.console import Console
from rich.panel import Panel
from urllib3.exceptions import HTTPError

from agent.tools import get_tools, list_pods
from evidence_common import PodEvidenceError
from tools.pod_listing import list_pods as collect_pods


def main() -> None:
    # kube-node-lease is normally empty of Pods in kind. Use another existing
    # empty namespace here if needed; this script creates no resources.
    namespaces = ["aiops", "kube-node-lease"]
    console = Console()
    tools = get_tools()
    tool_node = ToolNode(tools)

    console.print("Registered tools:", [registered.name for registered in tools])
    console.print("ToolNode registrations:", sorted(tool_node.tools_by_name))
    console.print(Panel(list_pods.name, title="Tool Name"))
    console.print(Panel(list_pods.description, title="Tool Description"))
    console.print("LLM-visible input schema:")
    console.print_json(json.dumps(list_pods.tool_call_schema.model_json_schema()))

    for namespace in namespaces:
        console.rule(f"Pods in {namespace}")
        try:
            result = collect_pods(namespace)
        except PodEvidenceError as error:
            console.print(f"Listing failed (Kubernetes status: {error.status}). Check kubeconfig and permissions.")
        except (HTTPError, OSError):
            console.print("Cannot connect to the Kubernetes API.")
        else:
            console.print_json(json.dumps(result))
            if not result["pods"] and not result["truncated"]:
                console.print("The listing contains no Pods.")


if __name__ == "__main__":
    main()
