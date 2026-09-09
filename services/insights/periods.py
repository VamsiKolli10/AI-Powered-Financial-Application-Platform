"""Period arithmetic for reporting windows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def month_start(when: datetime) -> datetime:
    return when.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)


def previous_month_start(when: datetime) -> datetime:
    start = month_start(when)
    return month_start(start - timedelta(days=1))


def week_start(when: datetime) -> datetime:
    start = when.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    return start - timedelta(days=start.weekday())


def period_bounds(period: str, reference: datetime) -> tuple[datetime, datetime, str]:
    """(start, end, label) for the requested period, ending at `reference`."""
    if period == "weekly":
        start = week_start(reference)
        return start, reference, f"{start:%Y-W%V}"
    start = month_start(reference)
    return start, reference, f"{start:%Y-%m}"


def previous_bounds(period: str, start: datetime) -> tuple[datetime, datetime]:
    """The comparison window immediately before `start`."""
    if period == "weekly":
        return start - timedelta(days=7), start
    return previous_month_start(start), start
