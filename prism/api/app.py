"""FastAPI application factory."""

import os
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from prism.api.routes import router
from prism.web.routes import web_router, _PUBLIC_PATHS
from prism.web.auth import COOKIE_NAME, validate_session

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

    # Check if auth is enabled (PRISM_ADMIN_PASSWORD set, disabled in test mode when conn is injected)
    auth_enabled = bool(os.environ.get("PRISM_ADMIN_PASSWORD")) and conn is None

    if auth_enabled and conn is None:
        # Auto-create admin on first run
        from prism.web.auth import create_admin
        create_admin(app.state.db, "admin", os.environ["PRISM_ADMIN_PASSWORD"])

    @app.middleware("http")
    async def middleware(request: Request, call_next):
        request.state.db = app.state.db

        # Auth check (skip for public paths, API, and static)
        if auth_enabled:
            path = request.url.path
            is_public = any(path.startswith(p) for p in _PUBLIC_PATHS) or path.startswith("/api")
            if not is_public:
                token = request.cookies.get(COOKIE_NAME)
                user = validate_session(app.state.db, token) if token else None
                if not user:
                    return RedirectResponse("/login", status_code=303)

        return await call_next(request)

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # API routes (existing)
    app.include_router(router, prefix="/api")

    # Web frontend routes
    app.include_router(web_router)

    # Start background slides worker (only in production, not tests)
    if conn is None:
        from prism.web.slides import start_slides_worker
        start_slides_worker(app.state.db)

    return app
