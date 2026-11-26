"""Authenticated investigation endpoint backed by the shared LangGraph agent."""

import asyncio
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import ensure_config
from langchain_core.tracers.context import run_collector_var, tracing_v2_callback_var
from langgraph.errors import GraphRecursionError
from langsmith import tracing_context

from agent.authorization import NamespaceAccessDenied
from agent.factory.model import ModelPayloadError, sanitize_model_reply, sanitize_prompt
from backend.auth.dependencies import CurrentUser, authorization_context
from backend.core.logging import log_event
from .schemas import InvestigateRequest, InvestigateResponse, InvestigationMetadata
from .telemetry import tool_metadata

router = APIRouter()


@router.post("/agent/investigate", response_model=InvestigateResponse, responses={
    500: {"model": InvestigateResponse, "description": "Investigation failed"},
    502: {"model": InvestigateResponse, "description": "Invalid model exchange"},
    503: {"model": InvestigateResponse, "description": "Investigation unavailable or graph step limit reached"},
})
async def investigate(payload: InvestigateRequest, request: Request, user: CurrentUser):
    """Run an independent investigation with local telemetry outside the graph."""
    context = authorization_context(user, payload.namespace)
    investigation_id = uuid4()
    started = perf_counter()
    messages = []
    operations = request.app.state.operations_log
    log_event(operations, "investigation_started", investigation_id=str(investigation_id))
    status, status_code, answer, error = "failed", 500, "Agent investigation failed.", None
    try:
        try:
            messages = [HumanMessage(content=sanitize_prompt(payload.message))]
        except ModelPayloadError:
            error, status_code, answer = "prompt_processing_failed", 503, "Investigation message could not be processed."
        except ValueError:
            error, status_code, answer = "invalid_prompt", 422, "Investigation message is too large or has no usable text."
        if error is None:
            if tracing_v2_callback_var.get() or run_collector_var.get() or ensure_config().get("callbacks"):
                error, status_code, answer = "tracing_unavailable", 503, "Investigation is unavailable."
            else:
                with tracing_context(enabled=False, parent=False):
                    async for state in request.app.state.agent.astream(
                        {"messages": messages}, stream_mode="values",
                        config={"metadata": {"investigation_id": str(investigation_id)}},
                        context=context,
                    ):
                        messages = state["messages"]
                answer = sanitize_model_reply(messages[-1], {}).text
                status, status_code = "completed", 200
    except NamespaceAccessDenied:
        error = "namespace_denied"
        status_code = 403
        answer = "Namespace access denied."
    except GraphRecursionError:
        error = "step_limit"
        status_code = 503
        answer = "Investigation reached the graph step limit; evidence may be incomplete."
    except ModelPayloadError:
        error, status_code, answer = "invalid_model_exchange", 502, "Model exchange could not be safely processed."
    except asyncio.CancelledError:
        error = "cancelled"
        raise
    except Exception:
        error = "investigation_failed"
    finally:
        metadata = InvestigationMetadata(
            duration_ms=(perf_counter() - started) * 1000, **tool_metadata(messages),
        )
        fields = {
            "investigation_id": str(investigation_id), "status": status,
            **metadata.model_dump(),
        }
        if error is not None:
            log_event(operations, "investigation_error", error=True, **fields, error_type=error)
        log_event(operations, "investigation_completed", **fields)
    response = InvestigateResponse(
        investigation_id=investigation_id, status=status, answer=answer, metadata=metadata,
    )
    return JSONResponse(status_code=status_code, content=response.model_dump(mode="json"))
