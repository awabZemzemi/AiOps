"""Read current account permissions for every protected request."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
import jwt

from agent.authorization import AuthorizationContext
from .models import User
from .schemas import Namespace, UserPublic
from .security import token_subject

bearer = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


def unauthorized():
    return HTTPException(401, "Invalid authentication credentials.", headers={"WWW-Authenticate": "Bearer"})


def current_user(request: Request, token: Annotated[str | None, Depends(bearer)]) -> UserPublic:
    if not token:
        raise unauthorized()
    try:
        subject = token_subject(token, request.app.state.auth_settings)
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None
    with request.app.state.sessions() as session:
        user = session.get(User, subject)
        if user is None or not user.is_active or user.role not in ("admin", "operator"):
            raise unauthorized()
        return UserPublic.model_validate(user)


CurrentUser = Annotated[UserPublic, Depends(current_user)]


def admin_user(user: CurrentUser) -> UserPublic:
    if user.role != "admin":
        raise HTTPException(403, "Administrator access required.")
    return user


AdminUser = Annotated[UserPublic, Depends(admin_user)]


def namespace_access(namespace: Namespace, user: CurrentUser):
    if namespace not in user.allowed_namespaces:
        raise HTTPException(403, "Namespace access denied.")


def authorization_context(user: UserPublic, namespace: str | None = None):
    allowed = frozenset(user.allowed_namespaces)
    if namespace is not None:
        if namespace not in allowed:
            raise HTTPException(403, "Namespace access denied.")
        allowed = frozenset([namespace])
    return AuthorizationContext(user.id, allowed)
