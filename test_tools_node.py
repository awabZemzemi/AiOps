"""Manual inspection: python test_tools_node.py.

Only constructs the ToolNode; no tools execute and no credentials are needed.
"""

from langgraph.prebuilt import ToolNode

from agent.nodes import AgentNode, create_tools_node


def main() -> None:
    expected = {"get_pod_info", "get_logs", "get_events", "get_metrics", "list_pods"}
    tool_node = create_tools_node()
    assert isinstance(tool_node, ToolNode)
    names = sorted(tool_node.tools_by_name)
    print("Registered ToolNode tools:")
    for name in names:
        print(f"- {name}")
    assert len(names) == 5 and set(names) == expected, f"Unexpected tools: {names}"
    print(f"PASS: {AgentNode.__name__} and create_tools_node imports work.")
    print("PASS: one prebuilt ToolNode contains exactly the five expected tools.")


if __name__ == "__main__":
    main()
