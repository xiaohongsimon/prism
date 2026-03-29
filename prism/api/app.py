"""FastAPI application factory."""

import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from prism.api.routes import router
from prism.web.routes import web_router

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"


def create_app(conn: Optional[sqlite3.Connection] = None) -> FastAPI:
    """Create FastAPI app, optionally injecting a DB connection (for testing)."""
    app = FastAPI(title="Prism", version="1.0")

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

    # Static files — must come before router includes so /static/ paths are served correctly
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # API routes (existing)
    app.include_router(router, prefix="/api")

    # Web frontend routes — after /api so /api/* takes precedence
    app.include_router(web_router)

    return app
