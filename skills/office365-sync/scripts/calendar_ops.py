#!/usr/bin/env python3
"""Query/create/update/delete calendar events with conflict checks."""

from __future__ import annotations

import argparse
import datetime as dt

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


def _serialize_event(event) -> dict:
    return {
        "event_id": event.object_id,
        "subject": event.subject,
        "start": dt_to_iso(event.start),
        "end": dt_to_iso(event.end),
        "location": event.location,
        "is_all_day": event.is_all_day,
        "modified": dt_to_iso(event.modified),
        "web_link": event.web_link,
    }


def _resolve_calendar(account, calendar_id: str | None):
    schedule = account.schedule()
    if calendar_id:
        calendar = schedule.get_calendar(calendar_id=calendar_id)
    else:
        calendar = schedule.get_default_calendar()
    if calendar is None:
        fail("Could not resolve target calendar", calendar_id=calendar_id)
    return calendar


def _parse_dt_arg(account, raw: str | None, field_name: str) -> dt.datetime | None:
    if raw is None:
        return None
    return parse_iso_datetime(
        raw,
        field_name=field_name,
        default_timezone=account.protocol.timezone,
    )


def _load_events(calendar, start: dt.datetime, end: dt.datetime, limit: int) -> list:
    events = calendar.get_events(
        limit=limit,
        include_recurring=True,
        start_recurring=start,
        end_recurring=end,
    )
    return list(events)


def _has_overlap(
    left_start: dt.datetime,
    left_end: dt.datetime,
    right_start: dt.datetime,
    right_end: dt.datetime,
) -> bool:
    return left_start < right_end and right_start < left_end


def _detect_conflicts(
    events: list,
    *,
    start: dt.datetime,
    end: dt.datetime,
    skip_event_id: str | None = None,
) -> list[dict]:
    conflicts: list[dict] = []
    for event in events:
        if skip_event_id and event.object_id == skip_event_id:
            continue
        if event.start is None or event.end is None:
            continue
        if _has_overlap(start, end, event.start, event.end):
            conflicts.append(_serialize_event(event))
    return conflicts


def _resolve_event(calendar, args, account):
    if args.event_id:
        event = calendar.get_event(args.event_id)
        return event, []

    if not args.lookup_subject:
        fail(
            "Missing event selector. Provide --event-id or --lookup-subject with --lookup-start/--lookup-end."
        )

    lookup_start = _parse_dt_arg(account, args.lookup_start, "lookup-start")
    lookup_end = _parse_dt_arg(account, args.lookup_end, "lookup-end")
    if lookup_start is None or lookup_end is None:
        fail("lookup-start and lookup-end are required when --event-id is not provided")
    if lookup_end <= lookup_start:
        fail("lookup-end must be after lookup-start")

    candidates = [
        event
        for event in _load_events(calendar, lookup_start, lookup_end, args.limit)
        if (event.subject or "").strip().casefold()
        == args.lookup_subject.strip().casefold()
    ]
    if len(candidates) == 1:
        return candidates[0], []
    if not candidates:
        return None, []
    return None, [_serialize_event(event) for event in candidates]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calendar operations: query/create/update/delete"
    )
    parser.add_argument("action", choices=["query", "create", "update", "delete"])
    parser.add_argument("--calendar-id", help="Target calendar id (default calendar if omitted)")
    parser.add_argument("--event-id", help="Target event id for update/delete")
    parser.add_argument("--lookup-subject", help="Lookup subject when --event-id is omitted")
    parser.add_argument("--lookup-start", help="Lookup window start datetime (ISO-8601)")
    parser.add_argument("--lookup-end", help="Lookup window end datetime (ISO-8601)")
    parser.add_argument("--subject", help="Event subject (create or update)")
    parser.add_argument("--body", help="Event body (create or update)")
    parser.add_argument("--location", help="Event location (create or update)")
    parser.add_argument("--start", help="Event start datetime (ISO-8601)")
    parser.add_argument("--end", help="Event end datetime (ISO-8601)")
    parser.add_argument("--subject-contains", help="Local filter for query result")
    parser.add_argument(
        "--allow-conflicts",
        action="store_true",
        help="Allow create/update even when conflict is detected",
    )
    parser.add_argument("--limit", type=int, default=200, help="Fetch limit")
    parser.add_argument(
        "--interactive-auth",
        action="store_true",
        help="Run interactive auth flow when token is missing/invalid",
    )
    args = parser.parse_args()
    assert_positive_limit(args.limit)

    account = build_account()
    access = ensure_access(
        account,
        required_scopes=["basic", "calendar_all"],
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
            "Missing required OAuth scopes for calendar operations.",
            exit_code=8,
            required_scope_aliases=access["required_scope_aliases"],
            required_scopes=access["required_scopes"],
            missing_scopes=access["missing_scopes"],
            granted_scopes=access["granted_scopes"],
            auth_hint=access["auth_hint"],
            identity=access["identity"],
        )

    calendar = _resolve_calendar(account, args.calendar_id)

    if args.action == "query":
        start = _parse_dt_arg(account, args.start, "start")
        end = _parse_dt_arg(account, args.end, "end")
        if start is None or end is None:
            fail("query requires --start and --end")
        if end <= start:
            fail("end must be after start")

        events = _load_events(calendar, start, end, args.limit)
        if args.subject_contains:
            needle = args.subject_contains.strip().casefold()
            events = [evt for evt in events if needle in (evt.subject or "").casefold()]
        emit(
            {
                "ok": True,
                "operation": "calendar_query",
                "calendar_id": calendar.calendar_id,
                "count": len(events),
                "events": [_serialize_event(event) for event in events],
            }
        )

    if args.action == "create":
        if not args.subject:
            fail("create requires --subject")
        start = _parse_dt_arg(account, args.start, "start")
        end = _parse_dt_arg(account, args.end, "end")
        if start is None or end is None:
            fail("create requires --start and --end")
        if end <= start:
            fail("end must be after start")

        conflicts = _detect_conflicts(
            _load_events(calendar, start, end, args.limit),
            start=start,
            end=end,
        )
        if conflicts and not args.allow_conflicts:
            fail(
                "Calendar conflict detected. Re-run with --allow-conflicts to force create.",
                exit_code=5,
                conflicts=conflicts,
            )

        event = calendar.new_event(subject=args.subject)
        event.start = start
        event.end = end
        if args.body is not None:
            event.body = args.body
        if args.location is not None:
            event.location = args.location

        if not event.save():
            fail("Failed to create event", exit_code=4)
        emit(
            {
                "ok": True,
                "operation": "calendar_create",
                "calendar_id": calendar.calendar_id,
                "event": _serialize_event(event),
                "conflicts_found": len(conflicts),
            }
        )

    if args.action == "update":
        event, candidates = _resolve_event(calendar, args, account)
        if event is None and candidates:
            fail(
                "Multiple matching events found. Provide --event-id.",
                exit_code=6,
                candidates=candidates,
            )
        if event is None:
            fail("No matching event found", exit_code=7)

        target_start = _parse_dt_arg(account, args.start, "start") or event.start
        target_end = _parse_dt_arg(account, args.end, "end") or event.end
        if target_start is None or target_end is None:
            fail("Target event has invalid time range")
        if target_end <= target_start:
            fail("end must be after start")

        conflicts = _detect_conflicts(
            _load_events(calendar, target_start, target_end, args.limit),
            start=target_start,
            end=target_end,
            skip_event_id=event.object_id,
        )
        if conflicts and not args.allow_conflicts:
            fail(
                "Calendar conflict detected. Re-run with --allow-conflicts to force update.",
                exit_code=5,
                conflicts=conflicts,
            )

        if args.subject is not None:
            event.subject = args.subject
        if args.body is not None:
            event.body = args.body
        if args.location is not None:
            event.location = args.location
        if args.start is not None:
            event.start = target_start
        if args.end is not None:
            event.end = target_end

        if not event.save():
            fail("Failed to update event", exit_code=4)
        emit(
            {
                "ok": True,
                "operation": "calendar_update",
                "calendar_id": calendar.calendar_id,
                "event": _serialize_event(event),
                "conflicts_found": len(conflicts),
            }
        )

    event, candidates = _resolve_event(calendar, args, account)
    if event is None and candidates:
        fail(
            "Multiple matching events found. Provide --event-id.",
            exit_code=6,
            candidates=candidates,
        )
    if event is None:
        fail("No matching event found", exit_code=7)

    event_data = _serialize_event(event)
    if not event.delete():
        fail("Failed to delete event", exit_code=4)
    emit(
        {
            "ok": True,
            "operation": "calendar_delete",
            "calendar_id": calendar.calendar_id,
            "event": event_data,
        }
    )


if __name__ == "__main__":
    main_guard(main)
