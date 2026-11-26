"""Build the two-node AiOps ReAct investigation graph."""

from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .factory import create_model
from .factory.model import sanitize_model_reply
from .state import AgentState
from .tools import get_tools
from .authorization import AuthorizationContext


def create_graph(profile: str = "gpt_oss_120b") -> CompiledStateGraph:
    """Compile the agent/tool loop with a default limit of 16 graph steps.

    Override the limit with config={"recursion_limit": ...} on invoke/stream.
    LangGraph raises GraphRecursionError if the run does not finish in time.
    """
    tools = get_tools()
    llm = create_model(profile, tools=tools)
    registered = {tool.name: tool for tool in tools}

    def call_agent(state: AgentState) -> dict:
        previous_ids = {call["id"] for message in state["messages"] if isinstance(message, AIMessage)
                        for call in message.tool_calls}
        return {"messages": [sanitize_model_reply(llm.invoke(state), registered, previous_ids)]}

    builder = StateGraph(AgentState, context_schema=AuthorizationContext)
    builder.add_node("agent", call_agent)
    builder.add_node("tools", ToolNode(tools, handle_tool_errors=False))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", tools_condition)
    builder.add_edge("tools", "agent")

    return builder.compile().with_config({"recursion_limit": 16})
