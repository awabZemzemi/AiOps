"""Build the two-node AiOps ReAct investigation graph."""

from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .factory import create_model
from .state import AgentState
from .tools import get_tools


def create_graph(profile: str = "gpt_oss_120b") -> CompiledStateGraph:
    """Compile the agent/tool loop with a default limit of 16 graph steps.

    Override the limit with config={"recursion_limit": ...} on invoke/stream.
    LangGraph raises GraphRecursionError if the run does not finish in time.
    """
    tools = get_tools()
    llm = create_model(profile, tools=tools)

    def call_agent(state: AgentState) -> dict:
        return {"messages": [llm.invoke(state)]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_agent)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    return builder.compile().with_config({"recursion_limit": 16})
