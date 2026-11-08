"""One registry for the implemented LangChain adapters."""

from langchain_core.tools import BaseTool

from .pod_info import get_pod_info
from .logs import get_logs
from .events import get_events
from .metrics import get_metrics
from .pod_listing import list_pods


def get_tools() -> list[BaseTool]:
    """Return all five read-only tools for both model binding and ToolNode."""
    return [get_pod_info, get_logs, get_events, get_metrics, list_pods]
