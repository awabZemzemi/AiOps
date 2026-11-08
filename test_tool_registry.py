"""Manual inspection: python test_tool_registry.py.

Requires GROQ_API_KEY in the environment to construct the configured model.
Checks local binding only: no model API request or Kubernetes call is made.
"""

from importlib.metadata import version
import json

from langchain_core.tools import BaseTool
from langgraph.prebuilt import ToolNode
from rich.console import Console
from rich.panel import Panel

from agent.model_factory import LLMFactory
from agent.tools import get_tools


def main() -> None:
    profile = "gpt_oss_120b"
    expected = {"get_pod_info", "get_logs", "get_events", "get_metrics", "list_pods"}
    console = Console()
    tools = get_tools()
    names = [registered.name for registered in tools]
    assert len(names) == 5 and set(names) == expected, f"Unexpected registry: {names}"
    assert all(isinstance(registered, BaseTool) for registered in tools)

    for package in ("langchain", "langchain-core", "langgraph", "langchain-groq"):
        console.print(f"{package}: {version(package)}")
    for registered in tools:
        console.print(Panel(registered.description, title=registered.name))
        console.print_json(json.dumps(registered.tool_call_schema.model_json_schema()))
    console.print("PASS: exactly the five expected LangChain tools are registered.")

    # Supply the exact same list to the prebuilt executor and the existing factory.
    tool_node = ToolNode(tools)
    assert set(tool_node.tools_by_name) == expected
    console.print("PASS: prebuilt ToolNode registers the same five tools.")

    pipeline = LLMFactory.create(profile, tools=tools)
    definitions = pipeline.last.kwargs["tools"]
    bound_names = [definition["function"]["name"] for definition in definitions]
    assert len(bound_names) == 5 and set(bound_names) == expected
    console.print("PASS: the configured Groq model binds all five tools (no API request made).")


if __name__ == "__main__":
    main()
