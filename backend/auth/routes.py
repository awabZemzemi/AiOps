"""Login, current identity, and administrator-only user management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Query, Request
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .accounts import lock_accounts, require_current_admin, update_account
from .dependencies import AdminUser, CurrentUser, unauthorized
from .models import User
from .schemas import TokenResponse, UserCreate, UserPatch, UserPublic
from .security import create_token, hash_password, password_hasher, verify_password

router = APIRouter()


@router.post("/auth/token", response_model=TokenResponse)
def login(request: Request,
          username: Annotated[str, Form(min_length=1, max_length=64)],
          password: Annotated[SecretStr, Form(min_length=1, max_length=1024)],
          grant_type: Annotated[str, Form(pattern="^password$")] = "password"):
    peer = request.client.host if request.client else "unknown"
    request.app.state.login_limiter.check(peer, username)
    with request.app.state.sessions() as session:
        user = session.scalar(select(User).where(User.username == username))
        encoded = user.password_hash if user else request.app.state.dummy_password_hash
        valid = verify_password(encoded, password.get_secret_value())
        if not valid or user is None or not user.is_active:
            raise unauthorized()
        if password_hasher.check_needs_rehash(encoded):
            user.password_hash = hash_password(password.get_secret_value())
            session.commit()
        settings = request.app.state.auth_settings
        return TokenResponse(access_token=create_token(user.id, settings),
                             expires_in=settings.access_token_minutes * 60)


@router.get("/auth/me", response_model=UserPublic)
def me(user: CurrentUser):
    return user


@router.post("/users", response_model=UserPublic, status_code=201)
def create_user(payload: UserCreate, request: Request, actor: AdminUser):
    encoded = hash_password(payload.password.get_secret_value())
    try:
        with request.app.state.sessions.begin() as session:
            lock_accounts(session)
            require_current_admin(session, actor.id)
            if session.scalar(select(User.id).where(User.username == payload.username)):
                raise HTTPException(409, "Username already exists.")
            user = User(**payload.model_dump(exclude={"password"}), password_hash=encoded)
            session.add(user)
            session.flush()
            result = UserPublic.model_validate(user)
        return result
    except IntegrityError:
        raise HTTPException(409, "Username already exists.") from None


@router.get("/users", response_model=list[UserPublic])
def list_users(request: Request, actor: AdminUser,
               offset: Annotated[int, Query(ge=0)] = 0,
               limit: Annotated[int, Query(ge=1, le=100)] = 100):
    with request.app.state.sessions() as session:
        return [UserPublic.model_validate(u) for u in session.scalars(
            select(User).order_by(User.created_at, User.id).offset(offset).limit(limit),
        )]


@router.patch("/users/{user_id}", response_model=UserPublic)
def patch_user(user_id: UUID, payload: UserPatch, request: Request, actor: AdminUser):
    with request.app.state.sessions.begin() as session:
        lock_accounts(session)
        require_current_admin(session, actor.id)
        result = UserPublic.model_validate(update_account(session, user_id, payload))
    return result
