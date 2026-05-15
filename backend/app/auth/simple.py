from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PasswordHash:
    salt: str
    iterations: int
    digest: str

    def serialize(self) -> dict[str, object]:
        return {"salt": self.salt, "iterations": self.iterations, "digest": self.digest}

    @classmethod
    def parse(cls, payload: dict[str, object]) -> "PasswordHash":
        return cls(str(payload["salt"]), int(payload["iterations"]), str(payload["digest"]))


class AdminAuth:
    def __init__(self, path: Path, iterations: int = 260_000) -> None:
        self.path = path
        self.iterations = iterations
        self.sessions: set[str] = set()

    def is_bootstrapped(self) -> bool:
        return self.path.exists()

    def bootstrap(self, password: str) -> None:
        if self.is_bootstrapped():
            raise ValueError("admin user already exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        password_hash = self._hash_password(password)
        self.path.write_text(json.dumps({"admin": password_hash.serialize()}, indent=2), encoding="utf-8")

    def verify(self, password: str) -> bool:
        if not self.is_bootstrapped():
            return False
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        stored = PasswordHash.parse(payload["admin"])
        candidate = self._hash_password(password, stored.salt, stored.iterations)
        return hmac.compare_digest(stored.digest, candidate.digest)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions.add(token)
        return token

    def has_session(self, token: str | None) -> bool:
        return token in self.sessions if token else False

    def logout(self, token: str | None) -> None:
        if token:
            self.sessions.discard(token)

    def _hash_password(
        self,
        password: str,
        salt: str | None = None,
        iterations: int | None = None,
    ) -> PasswordHash:
        raw_salt = base64.b64decode(salt) if salt else secrets.token_bytes(16)
        count = iterations or self.iterations
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), raw_salt, count)
        return PasswordHash(
            salt=base64.b64encode(raw_salt).decode("ascii"),
            iterations=count,
            digest=base64.b64encode(digest).decode("ascii"),
        )

