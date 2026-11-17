"""Environment configuration; never print settings containing credentials."""

from dataclasses import dataclass, field
import os


def database_url() -> str:
    return os.environ.get("AIOPS_DATABASE_URL", "sqlite:///./aiops.db")


@dataclass(frozen=True)
class AuthSettings:
    secret: str = field(repr=False)
    issuer: str = "aiops-backend"
    audience: str = "aiops-api"
    access_token_minutes: int = 15

    @classmethod
    def from_env(cls):
        secret = os.environ.get("AIOPS_JWT_SECRET", "")
        if len(secret.encode()) < 32 or not secret.strip():
            raise ValueError("AIOPS_JWT_SECRET must contain at least 32 bytes; generate a random secret.")
        minutes = int(os.environ.get("AIOPS_ACCESS_TOKEN_MINUTES", "15"))
        if not 1 <= minutes <= 60:
            raise ValueError("AIOPS_ACCESS_TOKEN_MINUTES must be between 1 and 60.")
        issuer = os.environ.get("AIOPS_JWT_ISSUER", "aiops-backend").strip()
        audience = os.environ.get("AIOPS_JWT_AUDIENCE", "aiops-api").strip()
        if not issuer or not audience:
            raise ValueError("JWT issuer and audience must not be empty.")
        return cls(secret, issuer, audience, minutes)
