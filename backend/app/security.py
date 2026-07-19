import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from fastapi import Cookie, Depends, Header, HTTPException, Response, status
from pymongo.database import Database

from .config import get_settings
from .mongo import get_db, utcnow


ph = PasswordHasher()
COOKIE_NAME = "race_admin_session"


def ensure_admin(db: Database) -> None:
    settings = get_settings()
    if db.admin_users.find_one({"username": settings.admin_username}):
        return
    db.admin_users.insert_one({
        "_id": secrets.token_hex(16),
        "username": settings.admin_username,
        "password_hash": ph.hash(settings.admin_password),
        "enabled": True,
        "created_at": utcnow(),
    })


def authenticate(db: Database, username: str, password: str) -> dict | None:
    user = db.admin_users.find_one({"username": username, "enabled": True})
    if not user:
        return None
    try:
        return user if ph.verify(user["password_hash"], password) else None
    except Exception:
        return None


def create_session(db: Database, user: dict, response: Response) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    session_id = hashlib.sha256(raw.encode()).hexdigest()
    csrf = secrets.token_urlsafe(24)
    db.admin_sessions.insert_one({
        "_id": session_id,
        "user_id": user["_id"],
        "csrf_token": csrf,
        "expires_at": utcnow() + timedelta(days=settings.session_days),
        "created_at": utcnow(),
    })
    response.set_cookie(
        COOKIE_NAME, raw, httponly=True, secure=settings.cookie_secure,
        samesite="lax", max_age=settings.session_days * 86400, path="/",
    )
    return csrf


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def get_admin(
    raw: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Database = Depends(get_db),
) -> dict:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin authentication required")
    admin_session = db.admin_sessions.find_one({"_id": hashlib.sha256(raw.encode()).hexdigest()})
    if not admin_session or _as_utc(admin_session["expires_at"]) <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.admin_users.find_one({"_id": admin_session["user_id"], "enabled": True})
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin account disabled")
    return user


def require_csrf(
    raw: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Database = Depends(get_db),
) -> None:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin authentication required")
    admin_session = db.admin_sessions.find_one({"_id": hashlib.sha256(raw.encode()).hexdigest()})
    if not admin_session or not csrf or not secrets.compare_digest(admin_session["csrf_token"], csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
