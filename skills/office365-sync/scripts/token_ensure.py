#!/usr/bin/env python3
"""Cron-friendly token health check and optional refresh."""

from __future__ import annotations

import argparse

from o365_common import (
    DEFAULT_SCOPE_ALIASES,
    build_account,
    emit,
    ensure_authenticated,
    fail,
    main_guard,
    parse_scopes,
)


def _is_token_expired(account) -> bool | None:
    username = account.con.username
    if not username:
        return None
    try:
        return bool(account.con.token_backend.token_is_expired(username=username))
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Ensure Office365 token health")
    parser.add_argument(
        "--scopes",
        help="Comma-separated scope aliases/full scopes; default uses skill scopes",
    )
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help="Run interactive auth flow when token is missing/invalid",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Always attempt refresh after authentication succeeds",
    )
    args = parser.parse_args()

    scopes = parse_scopes(args.scopes, DEFAULT_SCOPE_ALIASES)
    account = build_account()
    authenticated = ensure_authenticated(
        account,
        scopes=scopes,
        interactive=args.interactive_auth,
    )

    if not authenticated:
        fail(
            "Account is not authenticated. Run auth_bootstrap.py or add --interactive-auth.",
            exit_code=3,
            scopes=scopes,
        )

    expired_before = _is_token_expired(account)
    refreshed = False
    refresh_error = None

    should_refresh = bool(args.force_refresh)
    if expired_before is True:
        should_refresh = True

    if should_refresh:
        try:
            refreshed = bool(account.con.refresh_token())
        except Exception as exc:
            refresh_error = str(exc)

    expired_after = _is_token_expired(account)
    payload = {
        "ok": refresh_error is None,
        "authenticated": True,
        "refreshed": refreshed,
        "expired_before": expired_before,
        "expired_after": expired_after,
        "scopes": scopes,
    }
    if refresh_error:
        payload["error"] = "token_refresh_failed"
        payload["details"] = refresh_error
        emit(payload, exit_code=4)
    emit(payload)


if __name__ == "__main__":
    main_guard(main)

