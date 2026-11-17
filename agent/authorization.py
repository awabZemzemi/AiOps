"""Trusted per-run permissions, injected by LangGraph rather than the model."""

from dataclasses import dataclass
from uuid import UUID


class NamespaceAccessDenied(Exception):
    """Fail closed before any Kubernetes collector is called."""


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    allowed_namespaces: frozenset[str]

    def require_namespace(self, namespace: str) -> None:
        if namespace not in self.allowed_namespaces:
            raise NamespaceAccessDenied("Namespace access denied.")


def require_tool_namespace(runtime, namespace: str) -> None:
    context = runtime.context
    if not isinstance(context, AuthorizationContext):
        raise NamespaceAccessDenied("Trusted authorization context required.")
    context.require_namespace(namespace)
