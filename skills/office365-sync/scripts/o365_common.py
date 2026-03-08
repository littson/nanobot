#!/usr/bin/env python3
"""Shared helpers for the office365-sync skill scripts."""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import base64
from pathlib import Path
from typing import Sequence

DEFAULT_SCOPE_ALIASES = ["basic", "message_send", "calendar_all", "tasks_all"]
DEFAULT_TOKEN_PATH = Path.home() / ".nanobot" / "office365-sync"


class SkillConfigError(RuntimeError):
    """Raised when required skill runtime config is missing."""


def _import_o365():
    try:
        from O365 import Account, FileSystemTokenBackend
    except ImportError as exc:
        raise SkillConfigError(
            "Missing dependency: O365. Install with: pip install O365"
        ) from exc
    return Account, FileSystemTokenBackend


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SkillConfigError(f"Missing required environment variable: {name}")
    return value


def build_account():
    """Build an authenticated O365 Account object from environment variables."""
    Account, FileSystemTokenBackend = _import_o365()

    auth_flow = os.getenv("O365_AUTH_FLOW", "authorization").strip().lower()
    tenant_id = os.getenv("O365_TENANT_ID", "common").strip()
    client_id = _required_env("O365_CLIENT_ID")

    if auth_flow in {"public", "password"}:
        credentials = client_id
    else:
        client_secret = _required_env("O365_CLIENT_SECRET")
        credentials = (client_id, client_secret)

    token_path = Path(
        os.getenv("O365_TOKEN_PATH", str(DEFAULT_TOKEN_PATH))
    ).expanduser()
    token_filename = os.getenv("O365_TOKEN_FILENAME", "o365_token.txt")
    token_backend = FileSystemTokenBackend(
        token_path=token_path,
        token_filename=token_filename,
    )

    kwargs = {
        "auth_flow_type": auth_flow,
        "tenant_id": tenant_id,
        "token_backend": token_backend,
        "default_headers": {"Prefer": 'IdType="ImmutableId"'},
    }

    if auth_flow == "password":
        kwargs["username"] = _required_env("O365_USERNAME")
        kwargs["password"] = _required_env("O365_PASSWORD")

    return Account(credentials, **kwargs)


def parse_scopes(raw: str | None, fallback: Sequence[str] | None = None) -> list[str]:
    """Parse comma-separated scope aliases or full scopes."""
    fallback = list(fallback or DEFAULT_SCOPE_ALIASES)
    if not raw:
        return fallback
    parts = [part.strip() for part in raw.split(",")]
    scopes = [part for part in parts if part]
    return scopes or fallback


def ensure_authenticated(account, scopes: Sequence[str], interactive: bool) -> bool:
    """Ensure the account has a valid token, with optional interactive consent."""
    if account.is_authenticated:
        return True
    if not interactive:
        return False
    return bool(account.authenticate(requested_scopes=list(scopes)))


def _decode_jwt_claims(jwt_token: str | None) -> dict:
    token = (jwt_token or "").strip()
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _scope_name(scope: str) -> str:
    value = scope.strip()
    if not value:
        return ""
    if value.startswith("https://graph.microsoft.com/"):
        value = value[len("https://graph.microsoft.com/") :]
    elif "://" in value and "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value.casefold()


def resolve_required_scopes(account, scopes: Sequence[str]) -> list[str]:
    resolved = account.protocol.get_scopes_for(list(scopes))
    names = {_scope_name(scope) for scope in resolved}
    names.discard("")
    return sorted(names)


def get_token_scope_context(account, required_scopes: Sequence[str]) -> dict:
    if not account.con.token_backend.has_data:
        account.con.load_token_from_backend()

    username = account.con.username
    access = account.con.token_backend.get_access_token(username=username) or {}
    claims = _decode_jwt_claims(access.get("secret"))
    target_scopes = str(access.get("target", "")).split()
    if target_scopes:
        granted = {_scope_name(scope) for scope in target_scopes}
    else:
        granted = {_scope_name(scope) for scope in str(claims.get("scp", "")).split()}
    granted.discard("")

    required = resolve_required_scopes(account, required_scopes)
    missing = [scope for scope in required if scope not in granted]

    return {
        "required_scopes": required,
        "granted_scopes": sorted(granted),
        "missing_scopes": missing,
        "identity": {
            "cache_username": username,
            "tenant_id": claims.get("tid"),
            "idp": claims.get("idp"),
            "preferred_username": claims.get("preferred_username"),
            "upn": claims.get("upn"),
            "email": claims.get("email"),
            "aud": claims.get("aud"),
        },
    }


def ensure_access(
    account,
    *,
    required_scopes: Sequence[str],
    interactive: bool,
) -> dict:
    authenticated = ensure_authenticated(
        account,
        scopes=required_scopes,
        interactive=interactive,
    )
    auth_hint = (
        "python3 scripts/auth_bootstrap.py --force --scopes "
        + ",".join(required_scopes)
    )
    if not authenticated:
        return {
            "ok": False,
            "authenticated": False,
            "scope_ok": False,
            "auth_hint": auth_hint,
            "required_scope_aliases": list(required_scopes),
            "required_scopes": [],
            "granted_scopes": [],
            "missing_scopes": [],
            "identity": {},
        }

    ctx = get_token_scope_context(account, required_scopes)
    missing = list(ctx["missing_scopes"])

    if missing and interactive:
        authenticated = bool(account.authenticate(requested_scopes=list(required_scopes)))
        if authenticated:
            ctx = get_token_scope_context(account, required_scopes)
            missing = list(ctx["missing_scopes"])

    return {
        "ok": authenticated and not missing,
        "authenticated": authenticated,
        "scope_ok": not missing,
        "auth_hint": auth_hint,
        "required_scope_aliases": list(required_scopes),
        **ctx,
    }


def parse_iso_datetime(
    raw: str,
    *,
    field_name: str,
    default_timezone=None,
) -> dt.datetime:
    """Parse ISO datetime and enforce timezone."""
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise SkillConfigError(
            f"Invalid {field_name}: '{raw}'. Use ISO-8601 format."
        ) from exc

    if parsed.tzinfo is None:
        tzinfo = default_timezone or dt.timezone.utc
        parsed = parsed.replace(tzinfo=tzinfo)
    return parsed


def dt_to_iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def json_default(value):
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def emit(payload: dict, *, exit_code: int = 0) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=json_default))
    raise SystemExit(exit_code)


def fail(message: str, *, exit_code: int = 2, **extra) -> None:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    emit(payload, exit_code=exit_code)


def script_path(script_name: str) -> str:
    return str(Path(__file__).resolve().parent / script_name)


def sanitize_recipients(raw_values: list[str] | None) -> list[str]:
    recipients: list[str] = []
    for raw in raw_values or []:
        for part in raw.split(","):
            value = part.strip()
            if value:
                recipients.append(value)
    return recipients


def assert_positive_limit(limit: int) -> None:
    if limit <= 0:
        raise SkillConfigError("limit must be a positive integer")


def main_guard(main_func):
    try:
        main_func()
    except SkillConfigError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        fail("Operation cancelled by user", exit_code=130)
    except Exception as exc:  # pragma: no cover
        response = getattr(exc, "response", None)
        if response is not None:
            extra = {
                "details": str(exc),
                "http_status": response.status_code,
                "request_url": getattr(response.request, "url", None),
                "www_authenticate": response.headers.get("WWW-Authenticate"),
            }
            try:
                body = response.json()
                graph_error = body.get("error", body)
                extra["graph_error"] = graph_error
            except Exception:
                text = (response.text or "").strip()
                if text:
                    extra["response_text"] = text[:1200]
            fail("Unexpected failure", exit_code=1, **extra)
        fail("Unexpected failure", details=str(exc), exit_code=1)
