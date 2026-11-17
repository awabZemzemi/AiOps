"""Authenticated investigation endpoint backed by the shared LangGraph agent."""

import asyncio
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
from langgraph.errors import GraphRecursionError

from agent.authorization import NamespaceAccessDenied
from backend.auth.dependencies import CurrentUser, authorization_context
from backend.core.logging import log_event
from .schemas import InvestigateRequest, InvestigateResponse, InvestigationMetadata
from .telemetry import tool_metadata

router = APIRouter()


@router.post("/agent/investigate", response_model=InvestigateResponse, responses={
    500: {"model": InvestigateResponse, "description": "Investigation failed"},
    503: {"model": InvestigateResponse, "description": "Graph step limit reached"},
})
async def investigate(payload: InvestigateRequest, request: Request, user: CurrentUser):
    """Run an independent investigation with local telemetry outside the graph."""
    context = authorization_context(user, payload.namespace)
    investigation_id = uuid4()
    started = perf_counter()
    messages = [HumanMessage(content=payload.message)]
    operations = request.app.state.operations_log
    log_event(operations, "investigation_started", investigation_id=str(investigation_id))
    status, status_code, answer, error = "failed", 500, "Agent investigation failed.", None
    try:
        # Values contain the full reducer-managed history after every graph step.
        # The last emitted state survives recursion limits and later node failures.
        async for state in request.app.state.agent.astream(
            {"messages": messages}, stream_mode="values",
            config={"metadata": {"investigation_id": str(investigation_id)}},
            context=context,
        ):
            messages = state["messages"]
        final = messages[-1]
        if not isinstance(final, AIMessage) or final.tool_calls or not final.text:
            raise ValueError("Graph did not produce a final diagnostic answer.")
        answer = final.text
        status, status_code = "completed", 200
    except NamespaceAccessDenied as exc:
        error = exc
        status_code = 403
        answer = "Namespace access denied."
    except GraphRecursionError as exc:
        error = exc
        status_code = 503
        answer = "Investigation reached the graph step limit; evidence may be incomplete."
    except asyncio.CancelledError as exc:
        error = exc
        raise
    except Exception as exc:
        error = exc
    finally:
        metadata = InvestigationMetadata(
            duration_ms=(perf_counter() - started) * 1000, **tool_metadata(messages),
        )
        fields = {
            "investigation_id": str(investigation_id), "status": status,
            **metadata.model_dump(),
        }
        if error is not None:
            # Stack locations aid debugging without copying exception text/locals.
            frames = []
            traceback = error.__traceback__
            while traceback:
                frames.append({"file": traceback.tb_frame.f_code.co_filename,
                               "line": traceback.tb_lineno,
                               "function": traceback.tb_frame.f_code.co_name})
                traceback = traceback.tb_next
            log_event(operations, "investigation_error", error=True, **fields,
                      error_type=type(error).__name__, frames=frames)
        log_event(request.app.state.messages_log, "investigation_messages", **fields,
                  messages=messages_to_dict(messages))
        log_event(operations, "investigation_completed", **fields)
    response = InvestigateResponse(
        investigation_id=investigation_id, status=status, answer=answer, metadata=metadata,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))
