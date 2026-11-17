"""Argon2id passwords and strictly validated short-lived JWTs."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
import jwt

from .config import AuthSettings

ALGORITHM = "HS256"
password_hasher = PasswordHasher(type=Type.ID)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_token(user_id: UUID, settings: AuthSettings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": str(user_id), "iat": now, "nbf": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
        "iss": settings.issuer, "aud": settings.audience,
    }, settings.secret, algorithm=ALGORITHM)


def token_subject(token: str, settings: AuthSettings) -> UUID:
    claims = jwt.decode(token, settings.secret, algorithms=[ALGORITHM],
                        issuer=settings.issuer, audience=settings.audience,
                        options={"require": ["sub", "exp", "iat", "nbf", "iss", "aud"]})
    return UUID(claims["sub"])
