"""Unified FastAPI interface for evidence collection and agent investigations."""

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field, StringConstraints

from agent.graph import create_graph
# The graph's tool package sets up imports for the existing standalone collectors.
from api import router as diagnostic_router
from .logging_setup import configure_logging, log_event


class InvestigateRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InvestigationMetadata(BaseModel):
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    tools_used: list[str]
    tool_call_count: int = Field(ge=0)


class InvestigateResponse(BaseModel):
    investigation_id: UUID
    status: Literal["completed", "failed"]
    answer: str
    metadata: InvestigationMetadata


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the graph once at startup, before accepting requests."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    app.state.operations_log, app.state.messages_log = configure_logging()
    try:
        app.state.agent = create_graph()
        yield
    finally:
        for logger in (app.state.operations_log, app.state.messages_log):
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
                handler.close()


app = FastAPI(title="AiOps Backend", version="0.1.0", lifespan=lifespan)
app.include_router(diagnostic_router)


@app.post("/agent/investigate", response_model=InvestigateResponse, responses={
    500: {"model": InvestigateResponse, "description": "Investigation failed"},
    503: {"model": InvestigateResponse, "description": "Graph step limit reached"},
})
async def investigate(payload: InvestigateRequest, request: Request):
    """Run an independent investigation with local telemetry outside the graph."""
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
        ):
            messages = state["messages"]
        final = messages[-1]
        if not isinstance(final, AIMessage) or final.tool_calls or not final.text:
            raise ValueError("Graph did not produce a final diagnostic answer.")
        answer = final.text
        status, status_code = "completed", 200
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
