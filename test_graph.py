"""Manual end-to-end inspection: python test_graph.py.

Uses Groq and the current kubeconfig (kind-sentinelops for the lab).
Requires GROQ_API_KEY. Edit configuration and scenarios directly in main().
The graph performs read-only Kubernetes diagnostic calls selected by the model.
Rich output is also saved to logs/test_graph.log, replacing the previous run.
"""

from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.text import Text

from agent.graph import create_graph
from agent.state import AgentState


def main() -> None:
    profile = "gpt_oss_120b"
    recursion_limit = 24
    scenarios = [
        "Check the health of each Pod in namespace aiops and retrieve its metrics.",
    ]
    log_path = Path(__file__).resolve().parent / "logs" / "test_graph.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    console = Console(record=True)
    try:
        graph = create_graph(profile)

        for request in scenarios:
            console.rule(Text(request))
            initial_state: AgentState = {
                "messages": [HumanMessage(content=request)],
            }
            final_state = initial_state
            totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            usage_available = False

            for mode, event in graph.stream(
                initial_state,
                config={"recursion_limit": recursion_limit},
                stream_mode=["updates", "values"],
            ):
                if mode == "updates":
                    for node, update in event.items():
                        console.rule(f"Node: {node}")
                        for message in (update or {}).get("messages", []):
                            if isinstance(message, AIMessage):
                                if message.content:
                                    console.print(Panel(Text(str(message.content)), title="Agent response"))
                                for call in message.tool_calls:
                                    console.print(Panel(Pretty(call), title="Requested tool call"))
                                usage = message.usage_metadata
                                if not usage:
                                    provider_usage = message.response_metadata.get("token_usage", {})
                                    if provider_usage:
                                        usage = {
                                            "input_tokens": provider_usage.get("prompt_tokens", 0),
                                            "output_tokens": provider_usage.get("completion_tokens", 0),
                                            "total_tokens": provider_usage.get("total_tokens", 0),
                                        }
                                if usage:
                                    usage_available = True
                                    console.print("Token usage:", Pretty(usage))
                                    for key in totals:
                                        totals[key] += usage.get(key, 0)
                            elif isinstance(message, ToolMessage):
                                console.print(Panel(
                                    Text(str(message.content)),
                                    title=f"Tool result: {message.name} / {message.tool_call_id} / {message.status}",
                                ))
                else:
                    final_state = event

            final_message = final_state["messages"][-1]
            console.print(Panel(Text(str(final_message.content)), title="Final diagnosis / investigation outcome"))
            console.print("Total token usage:", Pretty(totals) if usage_available else "Not supplied by the provider")
    except GraphRecursionError:
        console.print(f"Investigation stopped at the graph step limit ({recursion_limit}); evidence may be incomplete.")
    finally:
        console.print(f"Saved test output to {log_path}")
        console.save_text(str(log_path), styles=False)


if __name__ == "__main__":
    main()
