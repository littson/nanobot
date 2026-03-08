#!/usr/bin/env python3
"""Query/create/update/delete Office365 ToDo tasks with safe lookup."""

from __future__ import annotations

import argparse

from o365_common import (
    assert_positive_limit,
    build_account,
    dt_to_iso,
    emit,
    ensure_access,
    fail,
    main_guard,
    parse_iso_datetime,
)


def _normalize_subject(value: str) -> str:
    return value.strip().casefold()


def _serialize_task(task) -> dict:
    return {
        "task_id": task.task_id,
        "subject": task.subject,
        "status": task.status,
        "is_completed": task.is_completed,
        "due": dt_to_iso(task.due),
        "completed": dt_to_iso(task.completed),
        "modified": dt_to_iso(task.modified),
    }


def _resolve_folder(todo, *, list_id: str | None, list_name: str | None):
    if list_id:
        folder = todo.get_folder(folder_id=list_id)
    elif list_name:
        folder = todo.get_folder(folder_name=list_name)
    else:
        folder = todo.get_default_folder()
    if folder is None:
        fail("Could not resolve target todo list", list_id=list_id, list_name=list_name)
    return folder


def _list_tasks(folder, limit: int) -> list:
    tasks = folder.get_tasks(batch=limit)
    return list(tasks)


def _find_duplicate_subject(folder, *, subject: str, limit: int, skip_task_id: str | None = None) -> list[dict]:
    needle = _normalize_subject(subject)
    duplicates: list[dict] = []
    for task in _list_tasks(folder, limit):
        if skip_task_id and task.task_id == skip_task_id:
            continue
        if _normalize_subject(task.subject or "") != needle:
            continue
        if task.status and task.status.casefold() == "completed":
            continue
        duplicates.append(_serialize_task(task))
    return duplicates


def _resolve_task(folder, *, task_id: str | None, lookup_subject: str | None, limit: int):
    if task_id:
        return folder.get_task(task_id), []
    if not lookup_subject:
        fail("Missing task selector. Provide --task-id or --lookup-subject.")

    needle = _normalize_subject(lookup_subject)
    matches = [
        task
        for task in _list_tasks(folder, limit)
        if _normalize_subject(task.subject or "") == needle
    ]
    if len(matches) == 1:
        return matches[0], []
    if not matches:
        return None, []
    return None, [_serialize_task(task) for task in matches]


def _parse_due(account, raw_due: str | None):
    if raw_due is None:
        return None
    return parse_iso_datetime(
        raw_due,
        field_name="due",
        default_timezone=account.protocol.timezone,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Todo operations: query/create/update/delete")
    parser.add_argument("action", choices=["query", "create", "update", "delete"])
    parser.add_argument("--list-id", help="Target list id")
    parser.add_argument("--list-name", help="Target list name")
    parser.add_argument("--task-id", help="Target task id for update/delete")
    parser.add_argument("--lookup-subject", help="Lookup subject when --task-id is omitted")
    parser.add_argument("--subject", help="Task subject (create or update)")
    parser.add_argument("--body", help="Task body (create or update)")
    parser.add_argument("--due", help="Task due datetime (ISO-8601, create or update)")
    parser.add_argument("--subject-contains", help="Local query filter")
    parser.add_argument("--status", choices=["notStarted", "completed"], help="Local query filter")
    parser.add_argument(
        "--allow-duplicates",
        action="store_true",
        help="Allow duplicate subject among incomplete tasks in the same list",
    )
    parser.add_argument("--mark-completed", action="store_true", help="Mark task completed")
    parser.add_argument("--mark-uncompleted", action="store_true", help="Mark task uncompleted")
    parser.add_argument("--limit", type=int, default=200, help="Fetch limit")
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help="Run interactive auth flow when token is missing/invalid",
    )
    args = parser.parse_args()
    assert_positive_limit(args.limit)

    if args.mark_completed and args.mark_uncompleted:
        fail("Use only one of --mark-completed or --mark-uncompleted")

    account = build_account()
    access = ensure_access(
        account,
        required_scopes=["basic", "tasks_all"],
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
            "Missing required OAuth scopes for todo operations.",
            exit_code=8,
            required_scope_aliases=access["required_scope_aliases"],
            required_scopes=access["required_scopes"],
            missing_scopes=access["missing_scopes"],
            granted_scopes=access["granted_scopes"],
            auth_hint=access["auth_hint"],
            identity=access["identity"],
        )

    todo = account.tasks()
    folder = _resolve_folder(todo, list_id=args.list_id, list_name=args.list_name)

    if args.action == "query":
        tasks = _list_tasks(folder, args.limit)
        if args.subject_contains:
            needle = args.subject_contains.strip().casefold()
            tasks = [task for task in tasks if needle in (task.subject or "").casefold()]
        if args.status:
            tasks = [task for task in tasks if (task.status or "") == args.status]
        emit(
            {
                "ok": True,
                "operation": "todo_query",
                "list_id": folder.folder_id,
                "list_name": folder.name,
                "count": len(tasks),
                "tasks": [_serialize_task(task) for task in tasks],
            }
        )

    if args.action == "create":
        if not args.subject:
            fail("create requires --subject")
        duplicates = _find_duplicate_subject(folder, subject=args.subject, limit=args.limit)
        if duplicates and not args.allow_duplicates:
            fail(
                "Duplicate task subject detected in this list. Re-run with --allow-duplicates to force create.",
                exit_code=5,
                duplicates=duplicates,
            )

        task = folder.new_task(subject=args.subject)
        if args.body is not None:
            task.body = args.body
        due = _parse_due(account, args.due)
        if due is not None:
            task.due = due
        if args.mark_completed:
            task.mark_completed()

        if not task.save():
            fail("Failed to create task", exit_code=4)
        emit(
            {
                "ok": True,
                "operation": "todo_create",
                "list_id": folder.folder_id,
                "list_name": folder.name,
                "task": _serialize_task(task),
                "duplicates_found": len(duplicates),
            }
        )

    if args.action == "update":
        task, candidates = _resolve_task(
            folder,
            task_id=args.task_id,
            lookup_subject=args.lookup_subject,
            limit=args.limit,
        )
        if task is None and candidates:
            fail(
                "Multiple matching tasks found. Provide --task-id.",
                exit_code=6,
                candidates=candidates,
            )
        if task is None:
            fail("No matching task found", exit_code=7)

        if args.subject is not None:
            duplicates = _find_duplicate_subject(
                folder,
                subject=args.subject,
                limit=args.limit,
                skip_task_id=task.task_id,
            )
            if duplicates and not args.allow_duplicates:
                fail(
                    "Duplicate task subject detected in this list. Re-run with --allow-duplicates to force update.",
                    exit_code=5,
                    duplicates=duplicates,
                )
            task.subject = args.subject
        if args.body is not None:
            task.body = args.body
        due = _parse_due(account, args.due)
        if due is not None:
            task.due = due
        if args.mark_completed:
            task.mark_completed()
        if args.mark_uncompleted:
            task.mark_uncompleted()

        if not task.save():
            fail("Failed to update task", exit_code=4)
        emit(
            {
                "ok": True,
                "operation": "todo_update",
                "list_id": folder.folder_id,
                "list_name": folder.name,
                "task": _serialize_task(task),
            }
        )

    task, candidates = _resolve_task(
        folder,
        task_id=args.task_id,
        lookup_subject=args.lookup_subject,
        limit=args.limit,
    )
    if task is None and candidates:
        fail(
            "Multiple matching tasks found. Provide --task-id.",
            exit_code=6,
            candidates=candidates,
        )
    if task is None:
        fail("No matching task found", exit_code=7)

    task_data = _serialize_task(task)
    if not task.delete():
        fail("Failed to delete task", exit_code=4)
    emit(
        {
            "ok": True,
            "operation": "todo_delete",
            "list_id": folder.folder_id,
            "list_name": folder.name,
            "task": task_data,
        }
    )


if __name__ == "__main__":
    main_guard(main)
