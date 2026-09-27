"""Days as the project counts them: Paris calendar days, bounded in UTC (ADR 022).

Every timestamp is stored in UTC. A day is still a day of Paris: the market, the households and the
sources all follow its clock. It has 96 quarter-hours, 92 when the clocks go forward and 100 when
they go back.
"""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
QUARTER_HOUR = timedelta(minutes=15)


def paris_day(instant: datetime) -> date:
    """The Paris day an instant belongs to: 22:30 UTC on 1 June is already 2 June in Paris."""
    if instant.utcoffset() is None:
        raise ValueError("an instant needs a time zone: without one, it could be any time")
    return instant.astimezone(PARIS).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC start and end of a Paris day: from its midnight to the next one."""
    # A datetime is also a date for Python: its own calendar date would slip in without a word.
    if isinstance(day, datetime):
        raise TypeError("a day is a date, not an instant: take its day with paris_day() first")
    start = datetime.combine(day, time(), PARIS)
    end = datetime.combine(day + timedelta(days=1), time(), PARIS)
    return start.astimezone(UTC), end.astimezone(UTC)


def quarter_hours(day: date) -> int:
    """How many quarter-hours a Paris day has: 96, or 92 and 100 when the clocks change."""
    start, end = day_bounds(day)
    # In UTC, subtracting gives the time that really passed. Between two Paris times, Python
    # would give the difference on the clock, 24 hours even on a changeover day.
    return (end - start) // QUARTER_HOUR


def never_happened(local: datetime) -> bool:
    """Whether a Paris wall-clock time never happened: 02:00 to 02:59 when the clocks go forward.

    Such a time has no instant of its own: Python gives it the offset of the hour before, so that
    02:30 becomes 01:30 UTC, which is 03:30 on the Paris clocks.
    """
    if local.tzinfo is not None:
        raise TypeError("a wall-clock time has no time zone: give the date and hour as read")
    return (
        local.replace(tzinfo=PARIS).astimezone(UTC).astimezone(PARIS).replace(tzinfo=None) != local
    )


def first_pass(instant: datetime) -> bool:
    """Whether an instant falls in the first pass of the Paris hour lived twice: 02:00 to 02:59,
    summer time, on the day the clocks go back."""
    local = instant.astimezone(PARIS)
    return local.fold == 0 and local.replace(fold=1).utcoffset() != local.utcoffset()


def every_day(first: date, last: date) -> Iterator[date]:
    """Each day from first to last, both included; none when last comes before first."""
    return (first + timedelta(days=n) for n in range((last - first).days + 1))


def paris_months(first: date, last: date) -> list[tuple[datetime, datetime]]:
    """The Paris months from the one holding `first` to the one holding `last`, bounded in UTC."""
    bounds = []
    month = first.replace(day=1)
    while month <= last:
        following = (month + timedelta(days=31)).replace(day=1)
        bounds.append((day_bounds(month)[0], day_bounds(following)[0]))
        month = following
    return bounds
