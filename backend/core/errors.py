"""Safe HTTP error responses that omit credentials and database details."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError


async def validation_error(request: Request, error: RequestValidationError):
    # FastAPI's default validation body echoes inputs, including bad passwords.
    return JSONResponse(status_code=422, content={"detail": [
        {key: item[key] for key in ("loc", "msg", "type") if key in item}
        for item in error.errors()
    ]})


async def database_error(request: Request, error: SQLAlchemyError):
    return JSONResponse(status_code=503, content={"detail": "Authentication database unavailable."})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(SQLAlchemyError, database_error)
