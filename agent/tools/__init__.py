"""LangChain tools backed by the existing diagnostic collectors."""

from pathlib import Path
import sys

from langchain_core.tools import BaseTool

# The standalone service uses flat imports. Set up access once for all adapters
# without repackaging the service or coupling adapters to an import in pod_info.
_DIAGNOSTIC_SERVICE = str(Path(__file__).resolve().parents[2] / "diagnostic-service")
if _DIAGNOSTIC_SERVICE not in sys.path:
    sys.path.insert(0, _DIAGNOSTIC_SERVICE)

from .pod_info import get_pod_info
from .logs import get_logs
from .events import get_events
from .metrics import get_metrics
from .pod_listing import list_pods


def get_tools() -> list[BaseTool]:
    """Return the same read-only tool set for model binding and ToolNode."""
    return [get_pod_info, get_logs, get_events, get_metrics, list_pods]


__all__ = ["get_pod_info", "get_logs", "get_events", "get_metrics", "list_pods", "get_tools"]
