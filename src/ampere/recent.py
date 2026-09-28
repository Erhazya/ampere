"""The shape of the export of the Data screen (ADR 029), which the daily job checks before writing
it and the API checks again before serving it. A light module: the API imports neither Polars
nor the sources."""

from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

# Where the export lives in the data folder, and its size at most: nine days of seven series weigh
# about 150 kB.
EXPORT = Path("exports") / "recent.json"
MAX_BYTES = 1_000_000
# A third-party name, once the export has taken out its control characters.
LABEL = 100
# Nine days of quarter-hours, with room: a longer series is not one of the export's.
MAX_POINTS = 1_000
# An instant in milliseconds since 1970, between 2000 and 2100.
Millis = Annotated[int, Field(ge=946_684_800_000, lt=4_102_444_800_000)]
Name = Annotated[str, Field(min_length=1, max_length=LABEL)]


class Shape(BaseModel):
    """Only the fields described here, which nobody changes once read."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Series(Shape):
    """The points of one quantity, as [instant, value] pairs, in the unit of the clean layer."""

    id: Literal[
        "price",
        "consumption",
        "rte_forecast",
        "co2",
        "solar",
        "temperature",
        "temperature_forecast",
    ]
    source: Literal["smard", "eco2mix", "openmeteo"]
    unit: Annotated[str, Field(max_length=16)]
    points: Annotated[list[tuple[Millis, FiniteFloat]], Field(max_length=MAX_POINTS)]


class Day(Shape):
    """A Paris day of the period, with its public holiday and its school holidays, if any."""

    day: date
    public_holiday: Name | None
    school_holidays: Name | None


class Source(Shape):
    """A source to cite: its name and licence, the date it published for its last update when its
    licence asks for it, and when Ampère received its last values."""

    id: Literal["smard", "eco2mix", "openmeteo", "school-holidays", "public-holidays"]
    name: Name
    licence: Annotated[str, Field(min_length=1, max_length=40)]
    updated_at: Millis | None
    received_at: Millis | None


class Recent(Shape):
    """The export: the period, from its first instant to its end, excluded, and what it holds."""

    generated_at: Millis
    start: Millis
    end: Millis
    series: Annotated[list[Series], Field(max_length=7)]
    days: Annotated[list[Day], Field(max_length=12)]
    sources: Annotated[list[Source], Field(max_length=5)]
