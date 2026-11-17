"""Explicit response allowlists keep credential fields out of the API."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

Namespace = Annotated[str, StringConstraints(
    min_length=1, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$",
)]
Username = Annotated[str, StringConstraints(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")]
Role = Literal["admin", "operator"]
Namespaces = Annotated[list[Namespace], Field(max_length=100)]


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Username
    password: SecretStr = Field(min_length=1, max_length=1024)
    role: Role = "operator"
    allowed_namespaces: Namespaces = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def password_length_for_role(self):
        if self.role == "operator" and len(self.password.get_secret_value()) < 12:
            raise ValueError("Operator passwords must contain at least 12 characters.")
        return self

    @field_validator("allowed_namespaces")
    @classmethod
    def unique_namespaces(cls, values):
        return sorted(set(values))


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role | None = None
    allowed_namespaces: Namespaces | None = None
    is_active: bool | None = None

    @field_validator("role", "allowed_namespaces", "is_active", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("Supply a value or omit the field.")
        return value


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    role: Role
    allowed_namespaces: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def utc_timestamps(cls, value):
        # SQLite stores UTC wall time without preserving tzinfo.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
