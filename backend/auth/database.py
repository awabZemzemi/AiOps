"""Session ownership and explicit migration readiness checks."""

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from .models import User, UserWriteLock


def make_engine(url: str):
    sqlite = url.startswith("sqlite:")
    return create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if sqlite else {},
                         hide_parameters=True, echo=False, pool_pre_ping=True)


def make_sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)


def check_schema(engine):
    try:
        with make_sessions(engine)() as session:
            session.execute(select(User.id).limit(1))
            if session.get(UserWriteLock, 1) is None:
                raise ValueError("Missing account-write lock.")
    except (SQLAlchemyError, ValueError):
        raise RuntimeError("Authentication database unavailable; run 'uv run alembic upgrade head'.") from None
