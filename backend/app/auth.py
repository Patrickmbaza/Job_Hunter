"""Single-user local authentication with PBKDF2 and signed HttpOnly sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import get_settings

PASSWORD_ITERATIONS = 600_000
SESSION_COOKIE = "jobhunter_session"
CSRF_COOKIE = "jobhunter_csrf"
CSRF_HEADER = "X-CSRF-Token"
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 14:
        raise ValueError("Choose a password with at least 14 characters")
    if len(password) > 1024:
        raise ValueError("Password is too long")
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        count = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= count <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _unb64(salt), count)
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError, base64.binascii.Error):
        return False


def authentication_configured() -> bool:
    return bool(get_settings().app_password_hash)


def _key() -> bytes:
    value = get_settings().app_password_hash
    if not value:
        raise HTTPException(status_code=503, detail="Local authentication has not been configured")
    return hmac.new(value.encode("utf-8"), b"personal-ai-job-hunter/session/v1", hashlib.sha256).digest()


def create_session() -> str:
    expires = int(time.time()) + get_settings().session_ttl_seconds
    payload = f"{expires}.{secrets.token_urlsafe(32)}"
    signature = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def session_valid(token: str | None) -> bool:
    if not token:
        return False
    try:
        expires_text, nonce, supplied_signature = token.split(".", 2)
        payload = f"{expires_text}.{nonce}"
        expected = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
        return int(expires_text) > int(time.time()) and hmac.compare_digest(expected, supplied_signature)
    except (ValueError, UnicodeError, HTTPException):
        return False


def create_csrf_token(session: str) -> str:
    nonce = secrets.token_urlsafe(24)
    signature = _b64(hmac.new(_key(), f"csrf:{session}:{nonce}".encode("ascii"), hashlib.sha256).digest())
    return f"{nonce}.{signature}"


def csrf_valid(session: str | None, cookie_value: str | None, header_value: str | None) -> bool:
    if not session or not session_valid(session) or not cookie_value or not header_value:
        return False
    if not hmac.compare_digest(cookie_value, header_value):
        return False
    try:
        nonce, supplied = cookie_value.split(".", 1)
        expected = _b64(hmac.new(_key(), f"csrf:{session}:{nonce}".encode("ascii"), hashlib.sha256).digest())
        return hmac.compare_digest(expected, supplied)
    except (ValueError, HTTPException):
        return False


def require_session(request: Request) -> None:
    if not authentication_configured():
        raise HTTPException(status_code=503, detail="Local authentication has not been configured")
    if not session_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Authentication required")


def require_same_origin(request: Request) -> None:
    expected_origin = get_settings().frontend_origin.rstrip("/")
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or not hmac.compare_digest(origin, expected_origin):
        raise HTTPException(status_code=403, detail="Request origin is not allowed")


def require_csrf(request: Request) -> None:
    require_session(request)
    require_same_origin(request)
    session = request.cookies.get(SESSION_COOKIE)
    if not csrf_valid(session, request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def check_login_rate_limit(client_id: str) -> None:
    now = time.monotonic()
    attempts = _attempts[client_id]
    while attempts and now - attempts[0] > 600:
        attempts.popleft()
    if len(attempts) >= 8:
        raise HTTPException(status_code=429, detail="Too many login attempts; wait before trying again")
    attempts.append(now)
    if len(_attempts) > 256:
        for key in list(_attempts)[:128]:
            if not _attempts[key] or now - _attempts[key][-1] > 600:
                _attempts.pop(key, None)
