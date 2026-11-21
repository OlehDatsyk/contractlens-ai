"""Authentication: registration, login, refresh-token rotation, logout, profile and password reset."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update

from app.api.deps import DB, CurrentUser, require_csrf_header
from app.core.config import get_settings
from app.core.rate_limit import check_identifier, client_ip, rate_limit
from app.core.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.models import PasswordResetToken, RefreshToken, Role, User, Workspace, WorkspaceMembership
from app.schemas import (
    ForgotPasswordIn,
    LoginIn,
    PasswordChange,
    ProfileUpdate,
    RegisterIn,
    ResetPasswordIn,
    TokenOut,
    UserOut,
)
from app.services import audit
from app.services.notifications import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger(__name__)
COOKIE = "cl_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE, token, max_age=s.refresh_token_days * 86400, httponly=True,
        secure=s.is_production or s.backend_url.startswith("https"), samesite="strict", path="/api/auth",
    )


def _issue(db: DB, user: User, response: Response, request: Request, family: uuid.UUID | None = None) -> TokenOut:
    s = get_settings()
    raw = new_opaque_token()
    db.add(RefreshToken(user_id=user.id, token_hash=hash_token(raw), family_id=family or uuid.uuid4(),
                        expires_at=datetime.now(UTC) + timedelta(days=s.refresh_token_days),
                        user_agent=(request.headers.get("user-agent") or "")[:255]))
    _set_refresh_cookie(response, raw)
    response.headers["Cache-Control"] = "no-store"
    return TokenOut(access_token=create_access_token(user.id, user.token_version), expires_in=s.access_token_minutes * 60)


def _check_password(password: str, email: str) -> None:
    problems = password_problems(password, email)
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(problems))


@router.post("/register", response_model=TokenOut, status_code=201,
             dependencies=[Depends(rate_limit("register", 5, 600))])
def register(body: RegisterIn, request: Request, response: Response, db: DB) -> TokenOut:
    s = get_settings()
    if not s.registration_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Registration is disabled on this server")
    email = body.email.lower()
    _check_password(body.password, email)
    if db.scalar(select(func.count()).select_from(User).where(User.email == email)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Registration could not be completed. If you already have an account, sign in or reset your password.")
    user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    ws = Workspace(name=(body.workspace_name or f"{body.full_name.split()[0]}'s workspace")[:120], created_by=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMembership(workspace_id=ws.id, user_id=user.id, role=Role.owner))
    audit.record(db, "user.register", user_id=user.id, workspace_id=ws.id, ip=client_ip(request))
    tokens = _issue(db, user, response, request)
    db.commit()
    return tokens


@router.post("/login", response_model=TokenOut, dependencies=[Depends(rate_limit("login", 20, 300))])
def login(body: LoginIn, request: Request, response: Response, db: DB) -> TokenOut:
    s = get_settings()
    email = body.email.lower()
    check_identifier("login", email, 10, 900)
    user = db.scalar(select(User).where(User.email == email))
    now = datetime.now(UTC)
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if user and user.locked_until and user.locked_until > now:
        verify_password(body.password, None)  # constant-ish timing
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later or reset your password.")
    if not verify_password(body.password, user.password_hash if user else None) or user is None or not user.is_active:
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_login_count = 0
            audit.record(db, "user.login_failed", user_id=user.id, ip=client_ip(request))
            db.commit()
        raise generic
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    audit.record(db, "user.login", user_id=user.id, ip=client_ip(request))
    tokens = _issue(db, user, response, request)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(require_csrf_header), Depends(rate_limit("refresh", 60, 60))])
def refresh(request: Request, response: Response, db: DB,
            cl_refresh: Annotated[str | None, Cookie()] = None) -> TokenOut:
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please sign in again.")
    if not cl_refresh or len(cl_refresh) > 200:
        raise invalid
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(cl_refresh)).with_for_update())
    now = datetime.now(UTC)
    if row is None:
        raise invalid
    if row.revoked_at is not None:
        # Reuse of a rotated token: assume theft and revoke the whole token family.
        db.execute(update(RefreshToken).where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=now))
        audit.record(db, "user.refresh_reuse_detected", user_id=row.user_id, ip=client_ip(request))
        db.commit()
        response.delete_cookie(COOKIE, path="/api/auth")
        raise invalid
    user = db.get(User, row.user_id)
    if row.expires_at < now or user is None or not user.is_active:
        raise invalid
    row.revoked_at = now
    tokens = _issue(db, user, response, request, family=row.family_id)
    db.commit()
    return tokens


@router.post("/logout", status_code=204, dependencies=[Depends(require_csrf_header)])
def logout(response: Response, db: DB, cl_refresh: Annotated[str | None, Cookie()] = None) -> Response:
    if cl_refresh:
        db.execute(update(RefreshToken).where(RefreshToken.token_hash == hash_token(cl_refresh), RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=datetime.now(UTC)))
        db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
def logout_all(user: CurrentUser, request: Request, response: Response, db: DB) -> Response:
    user.token_version += 1
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=datetime.now(UTC)))
    audit.record(db, "user.logout_all", user_id=user.id, ip=client_ip(request))
    db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, user: CurrentUser, db: DB) -> User:
    if body.full_name is not None:
        user.full_name = " ".join(body.full_name.split())
    if body.email_notifications is not None:
        user.email_notifications = body.email_notifications
    db.commit()
    return user


@router.post("/change-password", status_code=204, dependencies=[Depends(rate_limit("change_pw", 10, 600))])
def change_password(body: PasswordChange, user: CurrentUser, request: Request, response: Response, db: DB) -> Response:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The current password is incorrect")
    _check_password(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
               .values(revoked_at=datetime.now(UTC)))
    audit.record(db, "user.password_changed", user_id=user.id, ip=client_ip(request))
    db.commit()
    response.delete_cookie(COOKIE, path="/api/auth")
    response.status_code = 204
    return response


@router.post("/forgot-password", status_code=202, dependencies=[Depends(rate_limit("forgot", 5, 900))])
def forgot_password(body: ForgotPasswordIn, request: Request, db: DB) -> dict[str, str]:
    s = get_settings()
    email = body.email.lower()
    check_identifier("forgot", email, 3, 3600)
    message = {"detail": "If an account exists for this email, a password reset link has been sent."}
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        return message
    raw = new_opaque_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(raw),
                              expires_at=datetime.now(UTC) + timedelta(minutes=s.password_reset_minutes)))
    audit.record(db, "user.password_reset_requested", user_id=user.id, ip=client_ip(request))
    db.commit()
    link = f"{s.frontend_url.rstrip('/')}/reset-password?token={raw}"
    sent = send_email(user.email, "Reset your ContractLens AI password",
                      f"A password reset was requested for your ContractLens AI account.\n\nReset your password "
                      f"(valid for {s.password_reset_minutes} minutes):\n{link}\n\nIf you did not request this, ignore this email.")
    if not sent and s.environment == "development":
        # Development convenience only: no email provider is configured, so print the link to the local console.
        log.warning("DEV ONLY - password reset link (email not configured): %s", link)
    return message


@router.post("/reset-password", status_code=204, dependencies=[Depends(rate_limit("reset", 10, 900))])
def reset_password(body: ResetPasswordIn, request: Request, response: Response, db: DB) -> Response:
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token)).with_for_update())
    now = datetime.now(UTC)
    if row is None or row.used_at is not None or row.expires_at < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired")
    _check_password(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    user.failed_login_count = 0
    user.locked_until = None
    row.used_at = now
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
               .values(used_at=now))
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
    audit.record(db, "user.password_reset", user_id=user.id, ip=client_ip(request))
    db.commit()
    response.status_code = 204
    return response
