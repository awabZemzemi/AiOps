"""Account mutations under a database lock, independent of HTTP handlers."""

from fastapi import HTTPException
from sqlalchemy import func, select, update

from .models import User, UserWriteLock


def lock_accounts(session):
    result = session.execute(update(UserWriteLock).where(UserWriteLock.id == 1).values(
        generation=UserWriteLock.generation + 1,
    ))
    if result.rowcount != 1:
        raise RuntimeError("Account-write lock missing; run migrations.")


def require_current_admin(session, actor_id):
    """Recheck after locking; a concurrent demotion must take effect."""
    actor = session.get(User, actor_id)
    if actor is None or not actor.is_active:
        raise HTTPException(401, "Invalid authentication credentials.",
                            headers={"WWW-Authenticate": "Bearer"})
    if actor.role != "admin":
        raise HTTPException(403, "Administrator access required.")


def update_account(session, target_id, payload):
    user = session.get(User, target_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    changes = payload.model_dump(exclude_unset=True)
    losing_admin = user.is_active and user.role == "admin" and (
        changes.get("role", user.role) != "admin" or not changes.get("is_active", user.is_active)
    )
    if losing_admin:
        count = session.scalar(select(func.count()).select_from(User).where(
            User.is_active.is_(True), User.role == "admin",
        ))
        if count <= 1:
            raise HTTPException(409, "Cannot disable or demote the final active administrator.")
    for key, value in changes.items():
        setattr(user, key, sorted(set(value)) if key == "allowed_namespaces" else value)
    session.flush()
    return user
