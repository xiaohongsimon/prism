"""FastAPI application factory."""

import sqlite3
from typing import Optional

from fastapi import FastAPI, Request

from prism.api.routes import router


def create_app(conn: Optional[sqlite3.Connection] = None) -> FastAPI:
    """Create FastAPI app, optionally injecting a DB connection (for testing)."""
    app = FastAPI(title="Prism API", version="1.0")

    if conn is not None:
        app.state.db = conn
    else:
        from prism.config import settings
        from prism.db import get_connection
        app.state.db = get_connection(settings.db_path)

    @app.middleware("http")
    async def db_middleware(request: Request, call_next):
        request.state.db = app.state.db
        return await call_next(request)

    app.include_router(router, prefix="/api")
    return app
