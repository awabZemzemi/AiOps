"""Bounded, thread-safe single-process login limiting using the peer address.

Forwarded headers are deliberately ignored. Deployments with multiple workers
must also enforce shared limits at their trusted ingress (see docs).
"""

from collections import deque
import hashlib
from threading import Lock
from time import monotonic

from fastapi import HTTPException


class LoginLimiter:
    def __init__(self, *, window=300, account_limit=5, ip_limit=20, max_keys=10000):
        self.window = window
        self.account_limit = account_limit
        self.ip_limit = ip_limit
        self.max_keys = max_keys
        self._attempts = {}
        self._lock = Lock()

    def check(self, peer: str, username: str):
        now = monotonic()
        account = hashlib.sha256(username.encode()).hexdigest()
        keys = [("ip", peer), ("account", account)]
        with self._lock:
            for key in list(self._attempts):
                attempts = self._attempts[key]
                while attempts and attempts[0] <= now - self.window:
                    attempts.popleft()
                if not attempts:
                    del self._attempts[key]
            blocked = len(self._attempts) + sum(k not in self._attempts for k in keys) > self.max_keys
            for key, limit in zip(keys, (self.ip_limit, self.account_limit)):
                if len(self._attempts.get(key, ())) >= limit:
                    blocked = True
            if blocked:
                raise HTTPException(429, "Too many login attempts; try again later.",
                                    headers={"Retry-After": str(self.window)})
            for key in keys:
                self._attempts.setdefault(key, deque()).append(now)
