"""Shared offline fixtures: real migrations and authentication, temporary SQLite."""

from functools import lru_cache
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config

from backend.auth.config import AuthSettings
from backend.auth.database import make_engine, make_sessions
from backend.auth.models import User
from backend.auth.security import create_token, hash_password

TEST_PASSWORD = "offline-test-password-only"


@lru_cache(maxsize=1)
def test_password_hash():
    return hash_password(TEST_PASSWORD)


def prepare_auth(testcase, namespaces=None):
    directory = Path(testcase.enterContext(TemporaryDirectory(prefix="aiops-auth-")))
    testcase.enterContext(patch.dict(os.environ, {
        "AIOPS_DATABASE_URL": f"sqlite:///{directory / 'users.db'}",
        "AIOPS_JWT_SECRET": secrets.token_urlsafe(48),
        "AIOPS_JWT_ISSUER": "aiops-backend",
        "AIOPS_JWT_AUDIENCE": "aiops-api",
        "AIOPS_ACCESS_TOKEN_MINUTES": "15",
        "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
    }))
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    engine = make_engine(os.environ["AIOPS_DATABASE_URL"])
    testcase.addCleanup(engine.dispose)
    sessions = make_sessions(engine)
    with sessions.begin() as session:
        user = User(username="admin", password_hash=test_password_hash(), role="admin",
                    allowed_namespaces=namespaces if namespaces is not None else ["aiops", "other", "a" * 63])
        session.add(user)
        session.flush()
    headers = {"Authorization": f"Bearer {create_token(user.id, AuthSettings.from_env())}"}
    return sessions, user, headers
