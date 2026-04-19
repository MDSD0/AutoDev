from __future__ import annotations

import base64
import contextvars
import json
import secrets
import string
from typing import Any

COOKIE_NAME = "autodev_user"

_user_scope_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("autodev_user_scope", default=None)
_runtime_override_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "autodev_runtime_override",
    default=None,
)
_allow_server_secrets_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "autodev_allow_server_secrets",
    default=True,
)

_SAFE_CHARS = set(string.ascii_letters + string.digits + "-_")


def _sanitize_scope(value: str | None) -> str:
    raw = (value or "").strip()
    cleaned = "".join(ch for ch in raw if ch in _SAFE_CHARS)[:64]
    if cleaned:
        return cleaned
    return "u_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:20]


def ensure_user_scope(value: str | None) -> str:
    return _sanitize_scope(value)


def decode_runtime_override(header_value: str | None) -> dict[str, Any] | None:
    if not header_value:
        return None
    try:
        padded = header_value + "=" * (-len(header_value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def set_request_context(
    user_scope: str | None,
    runtime_override: dict[str, Any] | None = None,
    allow_server_secrets: bool = True,
) -> tuple[contextvars.Token, contextvars.Token, contextvars.Token]:
    user_token = _user_scope_var.set(user_scope)
    runtime_token = _runtime_override_var.set(runtime_override)
    allow_token = _allow_server_secrets_var.set(bool(allow_server_secrets))
    return user_token, runtime_token, allow_token


def reset_request_context(tokens: tuple[contextvars.Token, contextvars.Token, contextvars.Token]) -> None:
    user_token, runtime_token, allow_token = tokens
    _user_scope_var.reset(user_token)
    _runtime_override_var.reset(runtime_token)
    _allow_server_secrets_var.reset(allow_token)


def get_user_scope() -> str | None:
    return _user_scope_var.get()


def get_runtime_override() -> dict[str, Any] | None:
    return _runtime_override_var.get()


def allow_server_secrets() -> bool:
    return _allow_server_secrets_var.get()
