# Office365 Sync Permissions

Use least-privilege delegated scopes by default:

- `Mail.Send`: send email only
- `Calendars.ReadWrite`: query/create/update/delete calendar events
- `Tasks.ReadWrite`: query/create/update/delete todo tasks
- `offline_access`: obtain refresh token for long-lived auth
- `User.Read`: basic identity scope for auth flows

For `python-o365` scope aliases in this skill:

- `message_send` -> `Mail.Send`
- `calendar_all` -> `Calendars.ReadWrite`
- `tasks_all` -> `Tasks.ReadWrite`
- `basic` -> `User.Read`

Capability profiles:

- Todo only: `basic,tasks_all`
- Calendar only: `basic,calendar_all`
- Mail send only: `basic,message_send`
- Full capability: `basic,tasks_all,calendar_all,message_send`

Incremental consent command template:

```bash
python3 scripts/auth_bootstrap.py --force --scopes <comma-separated-scope-aliases>
```

If runtime script reports missing scopes, use the returned `auth_hint` command.

Environment variables:

- `O365_CLIENT_ID` (required)
- `O365_CLIENT_SECRET` (required for `authorization`/`credentials`)
- `O365_TENANT_ID` (default `common`)
- `O365_AUTH_FLOW` (default `authorization`)
- `O365_TOKEN_PATH` (default `~/.nanobot/office365-sync`)
- `O365_TOKEN_FILENAME` (default `o365_token.txt`)
