"""Invoke the configured agent pipeline and return state updates."""

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import Runnable

from ..state import AgentState


class AgentNode:
    def __init__(self, llm: Runnable, *, max_steps: int | None = None):
        if max_steps is not None and (
            isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1
        ):
            raise ValueError("max_steps must be a positive integer.")
        self.llm = llm
        self.max_steps = max_steps

    def __call__(self, state: AgentState) -> dict:
        if self.max_steps is not None and state["steps_count"] >= self.max_steps:
            return {
                "messages": self._limit_messages(state["messages"]),
                "steps_count": state["steps_count"],
            }

        response = self.llm.invoke(
            {
                "namespace": state["namespace"],
                "target_pod": state["target_pod"],
                "messages": state["messages"],
            }
        )
        steps_count = state["steps_count"] + 1
        messages = [response]
        if self.max_steps is not None and steps_count >= self.max_steps and response.tool_calls:
            messages.extend(self._limit_messages(messages))
        return {"messages": messages, "steps_count": steps_count}

    def _limit_messages(self, history: list) -> list:
        """Close pending calls without execution, then provide a terminal answer."""
        pending = {}
        for message in history:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    pending[call["id"]] = call
            elif isinstance(message, ToolMessage):
                pending.pop(message.tool_call_id, None)

        reason = f"the maximum agent step count ({self.max_steps}) was reached"
        return [
            *[
                ToolMessage(
                    content=f"Tool call cancelled because {reason}. This tool was not executed.",
                    name=call["name"], tool_call_id=call["id"], status="error",
                )
                for call in pending.values()
            ],
            AIMessage(content=f"Investigation stopped because {reason}. Collected evidence may be incomplete."),
        ]
