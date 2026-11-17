"""Initialize and close process-wide resources once per application lifespan."""

from contextlib import asynccontextmanager
from pathlib import Path
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI

from agent.graph import create_graph
from backend.auth.config import AuthSettings, database_url
from backend.auth.database import check_schema, make_engine, make_sessions
from backend.auth.rate_limit import LoginLimiter
from backend.auth.security import hash_password
from .logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the graph once at startup, before accepting requests."""
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    app.state.auth_settings = AuthSettings.from_env()
    engine = make_engine(database_url())
    try:
        check_schema(engine)
        app.state.sessions = make_sessions(engine)
        app.state.login_limiter = LoginLimiter()
        app.state.dummy_password_hash = hash_password(secrets.token_urlsafe(32))
        app.state.operations_log, app.state.messages_log = configure_logging()
        try:
            app.state.agent = create_graph()
            yield
        finally:
            for logger in (app.state.operations_log, app.state.messages_log):
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                    handler.close()
    finally:
        engine.dispose()
