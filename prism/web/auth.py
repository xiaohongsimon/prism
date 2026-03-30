"""Simple auth: password login + invite codes + session cookies."""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone


SESSION_DAYS = 30
COOKIE_NAME = "prism_session"


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def create_admin(conn: sqlite3.Connection, username: str, password: str):
    """Create the admin user (first-time setup)."""
    conn.execute(
        "INSERT OR IGNORE INTO auth_users (username, password_hash, role) VALUES (?, ?, 'admin')",
        (username, _hash(password)),
    )
    conn.commit()


def login(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    """Validate credentials, return session token or None."""
    row = conn.execute(
        "SELECT id FROM auth_users WHERE username = ? AND password_hash = ?",
        (username, _hash(password)),
    ).fetchone()
    if not row:
        return None

    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, row["id"], expires),
    )
    conn.commit()
    return token


def validate_session(conn: sqlite3.Connection, token: str) -> dict | None:
    """Check session token, return user dict or None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT s.user_id, s.expires_at, u.username, u.role "
        "FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] < _now():
        conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    return {"user_id": row["user_id"], "username": row["username"], "role": row["role"]}


def create_invite(conn: sqlite3.Connection, created_by: int) -> str:
    """Generate a one-time invite code."""
    code = secrets.token_urlsafe(8)
    conn.execute(
        "INSERT INTO invite_codes (code, created_by) VALUES (?, ?)",
        (code, created_by),
    )
    conn.commit()
    return code


def register_with_invite(conn: sqlite3.Connection, code: str, username: str, password: str) -> str | None:
    """Register using invite code, return session token or None."""
    row = conn.execute(
        "SELECT code FROM invite_codes WHERE code = ? AND used_by IS NULL",
        (code,),
    ).fetchone()
    if not row:
        return None

    conn.execute(
        "INSERT INTO auth_users (username, password_hash) VALUES (?, ?)",
        (username, _hash(password)),
    )
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "UPDATE invite_codes SET used_by = ?, used_at = ? WHERE code = ?",
        (user_id, _now(), code),
    )
    conn.commit()
    return login(conn, username, password)
