"""Build the two-node AiOps ReAct investigation graph."""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from .model_factory import LLMFactory
from .nodes import AgentNode, create_tools_node
from .state import AgentState
from .tools.registry import get_tools


def route_after_agent(state: AgentState) -> str:
    """Choose the next node without invoking tools, models, or state updates."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "tools"
    return "end"


def create_graph(profile: str = "gpt_oss_120b", *, max_steps: int = 8) -> CompiledStateGraph:
    """Compile one AgentNode and one prebuilt ToolNode with a model-turn limit.

    max_steps limits total AgentNode model invocations recorded in steps_count.
    The last allowed turn can finish normally; further tool requests receive
    cancellation observations and a terminal explanation instead of execution.
    """
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps must be a positive integer.")
    tools = get_tools()
    llm = LLMFactory.create(profile, tools=tools)

    builder = StateGraph(AgentState)
    builder.add_node("agent", AgentNode(llm, max_steps=max_steps))
    builder.add_node("tools", create_tools_node(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "end": END})
    builder.add_edge("tools", "agent")

    # LangGraph counts both node types, while our budget counts model turns.
    return builder.compile().with_config({"recursion_limit": 2 * max_steps + 1})
