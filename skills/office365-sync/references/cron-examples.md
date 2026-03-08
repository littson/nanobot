# Cron Examples

This skill expects users to install and use a cron skill for scheduling.

## Recommended Schedule

Run token health check every 30 minutes:

```cron
*/30 * * * * cd /path/to/nanobot/skills/office365-sync && /usr/bin/python3 scripts/token_ensure.py --force-refresh >> /tmp/office365_token_ensure.log 2>&1
```

Fallback auth check every morning:

```cron
0 9 * * * cd /path/to/nanobot/skills/office365-sync && /usr/bin/python3 scripts/token_ensure.py >> /tmp/office365_token_daily.log 2>&1
```

## Notes

- If refresh fails and output says re-auth is required, run:
  `python3 scripts/auth_bootstrap.py`
- Keep `O365_TOKEN_PATH` on persistent storage, not ephemeral tmp directories.
- Keep logs for troubleshooting consent expiry or tenant policy issues.

