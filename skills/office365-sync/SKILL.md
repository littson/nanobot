---
name: office365-sync
description: Send Office365 email and manage calendar/todo with python-o365 using on-demand queries, conflict checks, safe update/delete lookup, and cron-friendly OAuth token health checks. Use when users need to send mail, or query/create/update/delete calendar events and todo tasks without real-time subscriptions.
---

# Office365 Sync

## Overview

Run Office365 operations for three capabilities only:
1. Send email.
2. Query/create/update/delete calendar events with conflict checks.
3. Query/create/update/delete ToDo tasks with safe lookup before update/delete.

Use active query only. Do not use webhook subscriptions or real-time listeners.

## Capability Matrix

Use least privilege and grant scopes by capability:

- Todo only: `basic,tasks_all`
- Calendar only: `basic,calendar_all`
- Mail send only: `basic,message_send`
- Todo + Calendar: `basic,tasks_all,calendar_all`
- Full capability: `basic,tasks_all,calendar_all,message_send`

Bootstrap examples:

```bash
python3 scripts/auth_bootstrap.py --force --device-code --scopes basic,tasks_all
python3 scripts/auth_bootstrap.py --force --device-code --scopes basic,tasks_all,calendar_all,message_send
```

Runtime scripts enforce required scopes and return:
- `missing_scopes`
- `granted_scopes`
- `auth_hint` (ready-to-run re-consent command)

## Prerequisites

Set environment variables before running scripts:
- `O365_CLIENT_ID`
- `O365_CLIENT_SECRET` (required for `authorization`/`credentials` flow)
- `O365_TENANT_ID` (default: `common`)
- `O365_AUTH_FLOW` (default: `authorization`)
- `O365_TOKEN_PATH` (default: `~/.nanobot/office365-sync`)
- `O365_TOKEN_FILENAME` (default: `o365_token.txt`)

Install dependency:
- `pip install O365`

## Workflow

1. Bootstrap OAuth token once:
```bash
python3 scripts/auth_bootstrap.py
```
Notes:
- Script auto-opens browser by default to avoid malformed copy/paste auth URLs.
- If needed, override redirect URI with `--redirect-uri <uri>` (must exist in Entra app config).
- If your browser shows URL risk warning or returns `AADSTS50147`, rerun and avoid manually editing the auth URL.
- If `AADSTS50147` still persists, use device code flow (requires `O365_AUTH_FLOW=public`):
  `python3 scripts/auth_bootstrap.py --force --device-code`
- To inspect current token identity/scopes: `python3 scripts/auth_whoami.py`
2. Configure cron token health check (cron skill is required):
```bash
python3 scripts/token_ensure.py --force-refresh
```
3. Execute business operations with on-demand queries:
- send mail: `scripts/mail_send.py`
- calendar operations: `scripts/calendar_ops.py`
- todo operations: `scripts/todo_ops.py`

## Mail

Send-only behavior:
```bash
python3 scripts/mail_send.py \
  --to user@example.com \
  --subject "Status" \
  --body "Daily update"
```

Do not query or sync mailbox content in this skill.

## Calendar

Use these actions:
- `query`: list events in a time range.
- `create`: check overlap conflicts, then create.
- `update`: resolve event first (id or lookup), check conflicts, then update.
- `delete`: resolve event first (id or lookup), then delete.

Examples:
```bash
python3 scripts/calendar_ops.py query --start 2026-03-07T09:00:00+08:00 --end 2026-03-07T18:00:00+08:00
python3 scripts/calendar_ops.py create --subject "Demo" --start 2026-03-08T10:00:00+08:00 --end 2026-03-08T11:00:00+08:00
python3 scripts/calendar_ops.py update --event-id <event_id> --subject "Demo v2"
python3 scripts/calendar_ops.py delete --event-id <event_id>
```

When `--event-id` is missing for update/delete, provide lookup args:
- `--lookup-subject`
- `--lookup-start`
- `--lookup-end`

## Todo

Use these actions:
- `query`: list tasks in one list.
- `create`: check duplicate-subject conflicts in the target list, then create.
- `update`: resolve task first (id or lookup), then update.
- `delete`: resolve task first (id or lookup), then delete.

Examples:
```bash
python3 scripts/todo_ops.py query
python3 scripts/todo_ops.py create --subject "Prepare report" --due 2026-03-10T18:00:00+08:00
python3 scripts/todo_ops.py update --task-id <task_id> --subject "Prepare report v2"
python3 scripts/todo_ops.py delete --task-id <task_id>
```

When `--task-id` is missing for update/delete, provide `--lookup-subject`.

## Token And Cron

Use `scripts/token_ensure.py` from cron for proactive token health.
If token is expired and refresh fails, re-run `scripts/auth_bootstrap.py`.

See `references/cron-examples.md` for cron patterns.

## Conflict Rules

Read `references/conflict-rules.md` before changing conflict logic.

## Permission Notes

Read `references/permissions.md` before changing OAuth scopes.
