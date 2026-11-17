"""Read database credentials from the environment, never from versioned config."""

import os
from pathlib import Path

from alembic import context
from dotenv import load_dotenv

from backend.auth.config import database_url
from backend.auth.database import make_engine
from backend.auth.models import Base

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
os.umask(0o077)
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(database_url())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata,
                              compare_type=True, render_as_batch=engine.dialect.name == "sqlite")
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
