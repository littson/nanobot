# Conflict Rules

## Calendar Conflict

Treat two events as conflicting when:

`new_start < existing_end` AND `existing_start < new_end`

Implementation behavior:

- Run range query before create/update.
- Ignore self event id during update conflict checks.
- Block operation when conflicts exist unless `--allow-conflicts` is set.

## Todo Conflict

Treat todo conflict as duplicate active task subject in the same list:

- Compare subjects case-insensitively after trimming whitespace.
- Ignore completed tasks for duplicate checks.
- Ignore self task id during update duplicate checks.

Implementation behavior:

- Block create/update when duplicates exist unless `--allow-duplicates` is set.

## Safe Update/Delete Lookup

Always resolve target record before update or delete:

Calendar:

- Preferred: `--event-id`
- Fallback: `--lookup-subject` + `--lookup-start` + `--lookup-end`
- If multiple matches, stop and require explicit id

Todo:

- Preferred: `--task-id`
- Fallback: `--lookup-subject`
- If multiple matches, stop and require explicit id

