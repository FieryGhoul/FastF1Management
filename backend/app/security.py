import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from fastapi import Cookie, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import AdminSession, AdminUser


ph = PasswordHasher()
COOKIE_NAME = "race_admin_session"


def ensure_admin(db: Session) -> None:
    settings = get_settings()
    if db.scalar(select(AdminUser).where(AdminUser.username == settings.admin_username)):
        return
    db.add(AdminUser(username=settings.admin_username, password_hash=ph.hash(settings.admin_password)))
    db.commit()


def authenticate(db: Session, username: str, password: str) -> AdminUser | None:
    user = db.scalar(select(AdminUser).where(AdminUser.username == username, AdminUser.enabled.is_(True)))
    if not user:
        return None
    try:
        return user if ph.verify(user.password_hash, password) else None
    except Exception:
        return None


def create_session(db: Session, user: AdminUser, response: Response) -> str:
    settings = get_settings()
    raw = secrets.token_urlsafe(32)
    session_id = hashlib.sha256(raw.encode()).hexdigest()
    csrf = secrets.token_urlsafe(24)
    db.add(AdminSession(
        id=session_id,
        user_id=user.id,
        csrf_token=csrf,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.session_days),
    ))
    db.commit()
    response.set_cookie(
        COOKIE_NAME, raw, httponly=True, secure=settings.cookie_secure,
        samesite="lax", max_age=settings.session_days * 86400, path="/",
    )
    return csrf


def get_admin(
    raw: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> AdminUser:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin authentication required")
    session_id = hashlib.sha256(raw.encode()).hexdigest()
    admin_session = db.get(AdminSession, session_id)
    now = datetime.now(timezone.utc)
    if not admin_session or admin_session.expires_at.replace(tzinfo=timezone.utc) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = db.get(AdminUser, admin_session.user_id)
    if not user or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin account disabled")
    return user


def require_csrf(
    raw: str | None = Cookie(default=None, alias=COOKIE_NAME),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
) -> None:
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin authentication required")
    admin_session = db.get(AdminSession, hashlib.sha256(raw.encode()).hexdigest())
    if not admin_session or not csrf or not secrets.compare_digest(admin_session.csrf_token, csrf):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")

