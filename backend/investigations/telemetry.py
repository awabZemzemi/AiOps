"""Execution metadata derived from structured graph messages."""

from langchain_core.messages import AIMessage, ToolMessage


def tool_metadata(messages) -> dict:
    """Count requested calls once, including calls whose execution failed.

    Results are matched by call ID so parallel/repeated calls are counted without
    double counting their ToolMessages. Text and tool arguments are never parsed.
    """
    names, call_ids = [], set()
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                names.append(call["name"])
                call_ids.add(call.get("id"))
    for message in messages:
        if isinstance(message, ToolMessage) and message.tool_call_id not in call_ids:
            names.append(message.name)
            call_ids.add(message.tool_call_id)
    return {
        "tools_used": list(dict.fromkeys(name for name in names if name)),
        "tool_call_count": len(names),
    }
