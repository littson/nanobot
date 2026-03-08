#!/usr/bin/env python3
"""Inspect current O365 token identity and scopes."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json

from o365_common import build_account, emit, fail, main_guard


def _decode_claims(jwt_token: str) -> dict:
    parts = jwt_token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _to_iso_utc(epoch: int | None) -> str | None:
    if not epoch:
        return None
    return dt.datetime.utcfromtimestamp(epoch).isoformat() + "Z"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect current token identity/scopes")
    parser.add_argument(
        "--show-claims",
        action="store_true",
        help="Include full decoded JWT claims (without signature verification).",
    )
    args = parser.parse_args()

    account = build_account()
    if not account.con.token_backend.has_data and not account.con.load_token_from_backend():
        fail("No token data found. Run auth_bootstrap.py first.", exit_code=3)

    username = account.con.username
    access = account.con.token_backend.get_access_token(username=username) or {}
    token = access.get("secret")
    if not token:
        fail("No access token found in token backend.", exit_code=3)

    claims = _decode_claims(token)
    target = access.get("target")
    payload = {
        "ok": True,
        "cache_username": username,
        "tenant_id": claims.get("tid"),
        "idp": claims.get("idp"),
        "preferred_username": claims.get("preferred_username"),
        "upn": claims.get("upn"),
        "email": claims.get("email"),
        "aud": claims.get("aud"),
        "scp": claims.get("scp"),
        "target": target,
        "exp_utc": _to_iso_utc(claims.get("exp")),
    }
    if args.show_claims:
        payload["claims"] = claims
    emit(payload)


if __name__ == "__main__":
    main_guard(main)
