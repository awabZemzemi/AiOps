"""Initialize and close process-wide resources once per application lifespan."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import secrets

from dotenv import load_dotenv
from fastapi import FastAPI
from langchain_core.globals import set_debug, set_verbose

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
    set_debug(False)
    set_verbose(False)
    for name in ("groq", "groq._base_client", "httpx", "httpcore", "urllib3", "kubernetes", "client"):
        logging.getLogger(name).setLevel(logging.WARNING)
    app.state.auth_settings = AuthSettings.from_env()
    engine = make_engine(database_url())
    try:
        check_schema(engine)
        app.state.sessions = make_sessions(engine)
        app.state.login_limiter = LoginLimiter()
        app.state.dummy_password_hash = hash_password(secrets.token_urlsafe(32))
        app.state.operations_log = configure_logging()
        try:
            app.state.agent = create_graph()
            yield
        finally:
            for handler in app.state.operations_log.handlers[:]:
                app.state.operations_log.removeHandler(handler)
                handler.close()
    finally:
        engine.dispose()
