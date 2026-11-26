"""Create prompted chat models from independent, named profiles."""

import json
import os
import re

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import TypeAdapter

from diagnostic_service.security import sanitize_text
from diagnostic_service.security.sanitizer import MAX_OBSERVATION_BYTES, MAX_TEXT_BYTES, REDACTED, safe_identifier
from diagnostic_service.security.schemas import Label, PodName

from ..prompts import SYSTEM_PROMPT
from ..tools._common import project_observation


PROFILES = {
    "gpt_oss_120b": {
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "api_key_env": "GROQ_API_KEY",
        "system_prompt": SYSTEM_PROMPT,
    },
}
MAX_PROMPT_BYTES = 8 * 1024
MAX_HISTORY_BYTES = 256 * 1024
MAX_HISTORY_MESSAGES = 256
CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
REASONING = re.compile(r"<think\b[^>]*>.*?(?:</think\s*>|\Z)", re.I | re.S)
REDACTED_ASSIGNMENT = re.compile(r"[\w.-]+[\"']?[ \t]*[:=][ \t]*\[REDACTED\]")
LABEL = TypeAdapter(Label)
POD_NAME = TypeAdapter(PodName)


class ModelPayloadError(ValueError):
    """The model exchange could not be safely processed."""


def _has_text(value: str) -> bool:
    return any(character.isalnum() for character in REDACTED_ASSIGNMENT.sub("", value).replace(REDACTED, ""))


def sanitize_prompt(value: str) -> str:
    if type(value) is not str or len(value) > MAX_PROMPT_BYTES or len(value.encode()) > MAX_PROMPT_BYTES:
        raise ValueError("Investigation message is too large.")
    # Keep validation and sanitizer failure separate for the HTTP status.
    try:
        safe = sanitize_text(value)
    except Exception:
        raise ModelPayloadError("Investigation message could not be processed.") from None
    if not _has_text(safe):
        raise ValueError("Investigation message has no usable text.")
    return safe


def _project_call(call: dict, tools: dict, previous_ids: set[str]) -> dict:
    call_id = call.get("id")
    if (type(call_id) is not str or not CALL_ID.fullmatch(call_id)
            or call_id in previous_ids or not safe_identifier(call_id)):
        raise ModelPayloadError("Invalid diagnostic call identifier.")
    tool = tools[call["name"]]
    schema = tool.tool_call_schema
    if type(call["args"]) is not dict or len(call["args"]) > len(schema.model_fields):
        raise ModelPayloadError("Invalid diagnostic arguments.")
    args = schema.model_validate(call["args"], strict=True, extra="forbid").model_dump(exclude_unset=True)
    for key, schema in (("namespace", LABEL), ("pod_name", POD_NAME), ("container_name", LABEL)):
        if key in args and not safe_identifier(schema.validate_python(args[key], strict=True)):
            raise ModelPayloadError("Unsafe diagnostic target.")
    previous_ids.add(call_id)
    return {"name": tool.name, "args": args, "id": call_id}


def sanitize_model_reply(message: AIMessage, tools: dict, previous_ids=()) -> AIMessage:
    """Keep only safe assistant text and validated calls to the registered tools."""
    try:
        if not isinstance(message, AIMessage) or message.invalid_tool_calls or "function_call" in message.additional_kwargs:
            raise ModelPayloadError("Invalid diagnostic reply.")
        if isinstance(message.content, list) and any(
            not isinstance(block, str) and (not isinstance(block, dict)
            or block.get("type") not in {"text", "reasoning"}
            or (block.get("type") == "text" and not isinstance(block.get("text"), str)))
            for block in message.content
        ):
            raise ModelPayloadError("Unsupported diagnostic reply content.")
        value = message.text
        if len(value) > MAX_TEXT_BYTES or len(value.encode()) > MAX_TEXT_BYTES:
            raise ModelPayloadError("Diagnostic reply is too large.")
        text = sanitize_text(REASONING.sub("", value))
        if len(message.tool_calls) > MAX_HISTORY_MESSAGES:
            raise ModelPayloadError("Too many diagnostic calls.")
        ids = set(previous_ids)
        calls = [_project_call(call, tools, ids) for call in message.tool_calls]
        if not calls and not _has_text(text):
            raise ModelPayloadError("Diagnostic reply has no usable text.")
        return AIMessage(content=text, tool_calls=calls)
    except Exception:
        raise ModelPayloadError("Model reply could not be processed.") from None


def _project_history(state: dict, tools: dict) -> dict:
    """Recheck bounded history before the prompt/model pipeline can transmit it."""
    try:
        history = state["messages"]
        if (not isinstance(history, list) or not history or len(history) > MAX_HISTORY_MESSAGES
                or not isinstance(history[0], HumanMessage)):
            raise ModelPayloadError("Missing investigation prompt.")
        messages = [HumanMessage(content=sanitize_prompt(history[0].content))]
        seen, pending = set(), {}
        size = len(json.dumps(messages[0].model_dump()))
        for message in history[1:]:
            if isinstance(message, AIMessage) and not pending and not isinstance(messages[-1], AIMessage):
                reply = sanitize_model_reply(message, tools, seen)
                pending = {call["id"]: call for call in reply.tool_calls}
                seen.update(pending)
                messages.append(reply)
            elif isinstance(message, ToolMessage) and message.tool_call_id in pending:
                call = pending.pop(message.tool_call_id)
                if (message.name != call["name"] or type(message.content) is not str
                        or len(message.content) > MAX_OBSERVATION_BYTES):
                    raise ModelPayloadError("Invalid observation envelope.")
                envelope, failed = project_observation(call["name"], json.loads(message.content), call["args"])
                messages.append(ToolMessage(content=json.dumps(envelope), tool_call_id=call["id"],
                                            name=call["name"], status="error" if failed else "success"))
            else:
                raise ModelPayloadError("Invalid message sequence.")
            size += len(json.dumps(messages[-1].model_dump())) + 2
            if size > MAX_HISTORY_BYTES:
                raise ModelPayloadError("Investigation history is too large.")
        if pending or isinstance(messages[-1], AIMessage):
            raise ModelPayloadError("Incomplete tool exchange.")
        return {"messages": messages}
    except Exception:
        raise ModelPayloadError("Model request could not be processed.") from None


def create_model(profile: str = "gpt_oss_120b", tools=None) -> Runnable:
    """Build the selected prompt/model pipeline, optionally binding tools.

    Raises ValueError for an unknown profile or an unset/blank API key.
    """
    if profile not in PROFILES:
        available = ", ".join(sorted(PROFILES))
        raise ValueError(
            f"Unknown LLM profile {profile!r}. Available profiles: {available}."
        )

    settings = PROFILES[profile]
    api_key = os.environ.get(settings["api_key_env"], "").strip()
    if not api_key:
        raise ValueError(
            f"Missing configuration for LLM profile {profile!r}: "
            f"set the {settings['api_key_env']} environment variable."
        )

    llm = init_chat_model(
        model=settings["model"],
        model_provider=settings["provider"],
        api_key=api_key,
    )
    tools = list(tools or ())
    if tools:
        llm = llm.bind_tools(tools)

    prompt = ChatPromptTemplate.from_messages(
        [("system", settings["system_prompt"]), MessagesPlaceholder("messages")]
    )
    registered = {tool.name: tool for tool in tools}
    return RunnableLambda(lambda state: _project_history(state, registered)) | prompt | llm
