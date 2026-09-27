"""Days as the project counts them: Paris calendar days, bounded in UTC (ADR 022).

Every timestamp is stored in UTC. A day is still a day of Paris: the market, the households and the
sources all follow its clock. It has 96 quarter-hours, 92 when the clocks go forward and 100 when
they go back.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
QUARTER_HOUR = timedelta(minutes=15)


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC start and end of a Paris day: from its midnight to the next one."""
    start = datetime.combine(day, time(), PARIS)
    end = datetime.combine(day + timedelta(days=1), time(), PARIS)
    return start.astimezone(UTC), end.astimezone(UTC)


def quarter_hours(day: date) -> int:
    """How many quarter-hours a Paris day has: 96, or 92 and 100 when the clocks change."""
    start, end = day_bounds(day)
    # In UTC, subtracting gives the time that really passed. Between two Paris times, Python
    # would give the difference on the clock, 24 hours even on a changeover day.
    return (end - start) // QUARTER_HOUR
