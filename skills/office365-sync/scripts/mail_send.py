#!/usr/bin/env python3
"""Send Office365 email (send-only behavior)."""

from __future__ import annotations

import argparse

from o365_common import (
    build_account,
    emit,
    ensure_access,
    fail,
    main_guard,
    sanitize_recipients,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send Office365 email")
    parser.add_argument(
        "--to",
        action="append",
        required=True,
        help="Recipient email. Repeat or pass comma-separated values.",
    )
    parser.add_argument(
        "--cc",
        action="append",
        help="CC recipient email. Repeat or pass comma-separated values.",
    )
    parser.add_argument(
        "--bcc",
        action="append",
        help="BCC recipient email. Repeat or pass comma-separated values.",
    )
    parser.add_argument("--subject", required=True, help="Email subject")
    parser.add_argument("--body", required=True, help="Email body")
    parser.add_argument(
        "--body-type",
        choices=["text", "html"],
        default="text",
        help="Body type",
    )
    parser.add_argument(
        "--no-save-to-sent",
        action="store_true",
        help="Do not save the message to Sent Items",
    )
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help="Run interactive auth flow when token is missing/invalid",
    )
    args = parser.parse_args()

    to_recipients = sanitize_recipients(args.to)
    cc_recipients = sanitize_recipients(args.cc)
    bcc_recipients = sanitize_recipients(args.bcc)
    if not to_recipients:
        fail("At least one --to recipient is required")

    account = build_account()
    access = ensure_access(
        account,
        required_scopes=["basic", "message_send"],
        interactive=args.interactive_auth,
    )
    if not access["authenticated"]:
        fail(
            "Account is not authenticated. Run auth_bootstrap.py or add --interactive-auth.",
            exit_code=3,
            auth_hint=access["auth_hint"],
        )
    if not access["scope_ok"]:
        fail(
            "Missing required OAuth scopes for mail send.",
            exit_code=8,
            required_scope_aliases=access["required_scope_aliases"],
            required_scopes=access["required_scopes"],
            missing_scopes=access["missing_scopes"],
            granted_scopes=access["granted_scopes"],
            auth_hint=access["auth_hint"],
            identity=access["identity"],
        )

    mailbox = account.mailbox()
    message = mailbox.new_message()
    message.to.add(to_recipients)
    if cc_recipients:
        message.cc.add(cc_recipients)
    if bcc_recipients:
        message.bcc.add(bcc_recipients)

    message.subject = args.subject
    message.body = args.body
    message.body_type = "HTML" if args.body_type == "html" else "Text"

    sent = bool(message.send(save_to_sent_folder=(not args.no_save_to_sent)))
    if not sent:
        fail("Failed to send message", exit_code=4)

    emit(
        {
            "ok": True,
            "operation": "send_mail",
            "to": to_recipients,
            "cc": cc_recipients,
            "bcc": bcc_recipients,
            "subject": args.subject,
            "saved_to_sent": not args.no_save_to_sent,
        }
    )


if __name__ == "__main__":
    main_guard(main)
