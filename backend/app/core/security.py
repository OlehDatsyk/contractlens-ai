"""Password hashing, JWT handling, opaque tokens, signed download links and credential encryption."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults (memory-hard)
# A real hash of a random value, used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123",
    "qwertyuiop", "iloveyou", "letmein123", "admin123", "welcome123", "changeme", "abc12345",
}


def password_problems(password: str, email: str | None = None) -> list[str]:
    problems: list[str] = []
    if len(password) < 10:
        problems.append("Password must be at least 10 characters long")
    if len(password) > 128:
        problems.append("Password must be at most 128 characters long")
    classes = sum(
        [any(c.islower() for c in password), any(c.isupper() for c in password),
         any(c.isdigit() for c in password), any(not c.isalnum() for c in password)]
    )
    if classes < 3:
        problems.append("Use at least three of: lowercase, uppercase, digits, symbols")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("This password is too common")
    if email and email.split("@")[0].lower() in password.lower() and len(email.split("@")[0]) >= 4:
        problems.append("Password must not contain your email name")
    return problems


# --- JWT access tokens ------------------------------------------------------------------------------------------

def create_access_token(user_id: uuid.UUID, token_version: int) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "ver": token_version,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=s.access_token_minutes)).timestamp()),
        "jti": secrets.token_hex(8),
        "iss": "contractlens",
        "aud": "contractlens-api",
    }
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret_key,
            algorithms=[s.jwt_algorithm],  # explicit allow-list: rejects "none" and algorithm confusion
            audience="contractlens-api",
            issuer="contractlens",
            options={"require": ["exp", "sub", "iat", "type"]},
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


# --- Opaque tokens (refresh / reset / invitations) -----------------------------------------------------------

def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Keyed hash so that a leaked database alone cannot be used to forge lookups."""
    key = get_settings().jwt_secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


# --- Signed, short-lived download links ---------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_download(kind: str, object_id: uuid.UUID, user_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
    s = get_settings()
    expires = int(time.time()) + s.download_link_seconds
    body = f"{kind}:{object_id}:{user_id}:{workspace_id}:{expires}"
    sig = hmac.new(s.jwt_secret_key.encode(), b"dl|" + body.encode(), hashlib.sha256).digest()
    return _b64(body.encode()) + "." + _b64(sig)


def verify_download(token: str) -> tuple[str, uuid.UUID, uuid.UUID, uuid.UUID] | None:
    s = get_settings()
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _unb64(body_b64).decode()
        expected = hmac.new(s.jwt_secret_key.encode(), b"dl|" + body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        kind, oid, uid, wid, exp = body.split(":")
        if int(exp) < int(time.time()):
            return None
        return kind, uuid.UUID(oid), uuid.UUID(uid), uuid.UUID(wid)
    except (ValueError, UnicodeDecodeError):
        return None


# --- Encryption of stored credentials -----------------------------------------------------------------------

def _fernet() -> Fernet:
    s = get_settings()
    key = s.encryption_key
    if not key:
        # Development fallback: derive a stable key from the JWT secret. Production requires ENCRYPTION_KEY.
        key = base64.urlsafe_b64encode(hashlib.sha256(("enc|" + s.jwt_secret_key).encode()).digest()).decode()
    try:
        return Fernet(key.encode())
    except ValueError:
        # Any sufficiently random secret (e.g. a Render-generated value) is accepted and stretched to a Fernet key.
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("fernet|" + key).encode()).digest()))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str | None:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def mask_secret(value: str) -> str:
    return ("•" * 6 + value[-4:]) if len(value) > 8 else "•" * 6
