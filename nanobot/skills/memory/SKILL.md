---
name: memory
description: Two-layer memory system with grep-based recall.
always: true
---

# Memory

## Structure

- `memory/MEMORY.md` — Long-term facts (preferences, project context, relationships). Always loaded into your context.
- `memory/HISTORY.md` — Append-only event log. NOT loaded into context. Search it with grep. Each entry starts with [YYYY-MM-DD HH:MM].

## Search Past Events

```bash
grep -i "keyword" memory/HISTORY.md
```

Use the `exec` tool to run grep. Combine patterns: `grep -iE "meeting|deadline" memory/HISTORY.md`

## When to Update MEMORY.md

When the user asks you to remember something, you **MUST** immediately call `write_file` or `edit_file` to persist it. **NEVER** describe what you are about to do without actually calling the tool — describing is not the same as doing.

Facts to persist:
- User preferences ("I prefer dark mode")
- Project context ("The API uses OAuth2")
- Relationships ("Alice is the project lead")

## Workflow

1. Read `memory/MEMORY.md` first to avoid duplicates
2. Call `write_file` / `edit_file` to save the new fact
3. Re-read the file to confirm the write succeeded
4. Only then confirm to the user

If any step fails, report the error. Do **not** tell the user the fact was saved unless you have confirmed it via re-read.

## Auto-consolidation

Old conversations are automatically summarized and appended to HISTORY.md when the session grows large. Long-term facts are extracted to MEMORY.md. You don't need to manage this.
