"""FastAPI application composition; run with uvicorn backend.main:app."""

from fastapi import FastAPI

# Import lifespan first: the graph's tools enable the standalone collector imports.
from .core.lifespan import lifespan
from api import router as diagnostic_router
from .auth.routes import router as auth_router
from .core.errors import register_exception_handlers
from .investigations.routes import router as investigation_router

app = FastAPI(title="AiOps Backend", version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)
app.include_router(diagnostic_router)
app.include_router(auth_router)
app.include_router(investigation_router)
