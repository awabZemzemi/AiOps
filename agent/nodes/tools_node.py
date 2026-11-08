"""Construct the prebuilt executor from the centralized tool registry."""

from langgraph.prebuilt import ToolNode
from langchain_core.tools import BaseTool

from ..tools.registry import get_tools


def create_tools_node(tools: list[BaseTool] | None = None) -> ToolNode:
    """Create one ToolNode for all five registered diagnostic tools."""
    return ToolNode(get_tools() if tools is None else tools)
