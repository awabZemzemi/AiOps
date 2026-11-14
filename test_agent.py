"""Manual inspection: python test_agent.py.

Edit the profile and state below. Requires GROQ_API_KEY in the environment.
Prints tool metadata and asks the model for its next action; no tools execute.
"""

import json

from rich.console import Console
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table

from langchain_core.messages import HumanMessage

from agent.factory import create_model
from agent.state import AgentState
from agent.tools import get_tools


console = Console()


def main() -> None:
    profile = "gpt_oss_120b"
    tools = get_tools()

    state: AgentState = {
        "messages": [
            HumanMessage(
                content="Can you check whether Pod crash is running in namespace aiops?"
            )
        ],
    }

    console.rule("[bold cyan]AiOps — Agent Model Test")

    # Input
    info = Table(show_header=False, box=None, padding=(0, 2))
    info.add_column("Field", style="cyan")
    info.add_column("Value", style="white")

    info.add_row("Model profile", profile)
    info.add_row("User request", state["messages"][0].content)

    console.print(Panel(info, title="Initial State", border_style="blue"))

    # ToolRuntime is injected by the executor and excluded from this LLM schema.
    for registered_tool in tools:
        console.print(Panel(registered_tool.name, title="Registered Tool Name", border_style="cyan"))
        console.print(Panel(registered_tool.description, title="Tool Description", border_style="cyan"))
        console.print("[bold cyan]Input Schema Exposed to the LLM[/bold cyan]")
        console.print_json(json.dumps(registered_tool.tool_call_schema.model_json_schema()))

    # Execute
    llm = create_model(profile, tools=tools)

    with console.status("[cyan]Invoking Agent Model..."):
        response = llm.invoke(state)

    # LLM response
    console.print(
        Panel(
            str(response.content) or "[dim]No text response[/dim]",
            title="LLM Response",
            border_style="green",
        )
    )

    # Tool calls
    if response.tool_calls:
        for index, call in enumerate(response.tool_calls, start=1):
            console.print(
                Panel(
                    Pretty(call),
                    title=f"Tool Call #{index}",
                    border_style="yellow",
                )
            )
    else:
        console.print(
            Panel(
                "[dim]No tool calls requested[/dim]",
                title="Tool Calls",
                border_style="yellow",
            )
        )

    console.rule("[bold green]Execution Complete")


if __name__ == "__main__":
    main()
