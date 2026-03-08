#!/usr/bin/env python3
"""Initialize OAuth consent and persist O365 token."""

from __future__ import annotations

import argparse
import webbrowser
from urllib.parse import parse_qs, urlparse

from o365_common import (
    DEFAULT_SCOPE_ALIASES,
    SkillConfigError,
    build_account,
    emit,
    main_guard,
    parse_scopes,
)


def _run_device_code_flow(account, scopes: list[str]) -> tuple[bool, dict]:
    resolved_scopes = account.protocol.get_scopes_for(scopes)
    flow = account.con.msal_client.initiate_device_flow(scopes=resolved_scopes)
    if "user_code" not in flow:
        raise SkillConfigError(
            f"Failed to create device code flow: {flow.get('error') or 'unknown_error'}"
        )
    message = flow.get("message")
    if message:
        print(message)
    else:
        verify_uri = flow.get("verification_uri")
        user_code = flow.get("user_code")
        print(f"Open {verify_uri} and enter code {user_code}")

    result = account.con.msal_client.acquire_token_by_device_flow(flow)
    success = "access_token" in result
    extra: dict = {"auth_method": "device_code"}
    if success:
        account.con.token_backend.save_token()
        account.con.load_token_from_backend()
        return True, extra

    extra["error"] = result.get("error")
    extra["error_description"] = result.get("error_description")
    return False, extra


def _normalize_auth_response(raw: str, *, redirect_uri: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise SkillConfigError("No authenticated URL was provided.")

    if value.startswith("?"):
        value = f"{redirect_uri}{value}"
    elif "://" not in value and ("code=" in value or "error=" in value):
        value = f"{redirect_uri}?{value.lstrip('?')}"

    parsed = urlparse(value)
    if parsed.username or parsed.password:
        raise SkillConfigError(
            "Authenticated URL contains embedded credentials. "
            "Use the original redirect URL from Microsoft without modifications."
        )

    if parsed.fragment and not parsed.query:
        fragment_qs = parse_qs(parsed.fragment)
        if "code" in fragment_qs or "error" in fragment_qs:
            value = f"{value.split('#', 1)[0]}?{parsed.fragment}"
            parsed = urlparse(value)

    query = parse_qs(parsed.query)
    if "code" not in query and "error" not in query:
        raise SkillConfigError(
            "Authenticated URL is missing OAuth 'code'/'error' query params. "
            "This usually means the URL was truncated or altered during copy/paste."
        )
    return value


def _prompt_auth_response(consent_url: str, *, open_browser: bool) -> str:
    print("Open this consent URL in your browser and finish sign-in:")
    print(consent_url)
    if open_browser:
        opened = False
        try:
            opened = bool(webbrowser.open(consent_url, new=2))
        except Exception:
            opened = False
        if opened:
            print("Browser opened automatically. Continue in that window.")
        else:
            print("Automatic browser open failed. Copy the URL manually.")
    return input("Paste the full redirected URL here:\n").strip()


def _run_auth_code_flow(
    account,
    scopes: list[str],
    *,
    redirect_uri: str,
    auth_response_url: str | None,
    open_browser: bool,
    debug_auth_url: bool,
) -> tuple[bool, dict]:
    consent_url, flow = account.get_authorization_url(scopes, redirect_uri=redirect_uri)
    extra: dict = {"auth_method": "auth_code"}
    if debug_auth_url:
        params = parse_qs(urlparse(consent_url).query)
        code_challenge = params.get("code_challenge", [""])[0]
        extra["code_challenge_length"] = len(code_challenge)
        extra["code_challenge_method"] = (params.get("code_challenge_method") or [None])[
            0
        ]
        print(
            "Auth URL debug: "
            f"code_challenge_len={extra['code_challenge_length']}, "
            f"method={extra['code_challenge_method']}"
        )
    raw_response = auth_response_url or _prompt_auth_response(
        consent_url, open_browser=open_browser
    )
    normalized_response = _normalize_auth_response(raw_response, redirect_uri=redirect_uri)
    success = bool(account.request_token(normalized_response, flow=flow))
    return success, extra


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap Office365 OAuth token")
    parser.add_argument(
        "--scopes",
        help="Comma-separated scope aliases/full scopes; default uses skill scopes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force auth flow even when token is already valid",
    )
    parser.add_argument(
        "--redirect-uri",
        help="Override redirect URI. Must match an app registration redirect URI.",
    )
    parser.add_argument(
        "--auth-response-url",
        help="Provide redirected callback URL directly (skips interactive prompt).",
    )
    parser.add_argument(
        "--no-browser-open",
        action="store_true",
        help="Do not auto-open the consent URL in browser.",
    )
    parser.add_argument(
        "--device-code",
        action="store_true",
        help="Use OAuth device code flow (recommended if AADSTS50147 persists).",
    )
    parser.add_argument(
        "--debug-auth-url",
        action="store_true",
        help="Print auth URL debug fields (code_challenge length/method only).",
    )
    args = parser.parse_args()

    scopes = parse_scopes(args.scopes, DEFAULT_SCOPE_ALIASES)
    account = build_account()
    auth_flow = account.con.auth_flow_type

    if account.is_authenticated and not args.force:
        token_path = getattr(account.con.token_backend, "token_path", None)
        emit(
            {
                "ok": True,
                "status": "already_authenticated",
                "auth_flow": auth_flow,
                "scopes": scopes,
                "token_path": str(token_path) if token_path else None,
            }
        )

    extra: dict = {}
    if args.device_code:
        if auth_flow != "public":
            raise SkillConfigError(
                "--device-code requires O365_AUTH_FLOW=public "
                "(and Entra app public client flow enabled)."
            )
        success, extra = _run_device_code_flow(account, scopes)
    elif auth_flow in {"authorization", "public"}:
        redirect_uri = args.redirect_uri or account.con.oauth_redirect_url
        success, extra = _run_auth_code_flow(
            account,
            scopes,
            redirect_uri=redirect_uri,
            auth_response_url=args.auth_response_url,
            open_browser=not args.no_browser_open,
            debug_auth_url=args.debug_auth_url,
        )
    else:
        success = bool(account.authenticate(requested_scopes=scopes))

    token_path = getattr(account.con.token_backend, "token_path", None)
    payload = {
        "ok": success,
        "status": "authenticated" if success else "auth_failed",
        "auth_flow": auth_flow,
        "scopes": scopes,
        "token_path": str(token_path) if token_path else None,
    }
    payload.update(extra)
    if not success and auth_flow in {"authorization", "public"}:
        payload["hint"] = (
            "If browser shows URL risk warning or AADSTS50147, re-run without manually "
            "editing the consent URL, prefer auto-open, or use --device-code."
        )
    emit(payload, exit_code=0 if success else 3)


if __name__ == "__main__":
    main_guard(main)
