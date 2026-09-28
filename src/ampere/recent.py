"""The shape of the export of the Data screen (ADR 029), which the daily job checks before writing
it and the API checks again before serving it. A light module: the API imports neither Polars
nor the sources."""

import unicodedata
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

# Where the export lives in the data folder, and its size at most: nine days of seven series weigh
# about 150 kB.
EXPORT = Path("exports") / "recent.json"
MAX_BYTES = 1_000_000
# A third-party name, once the export has taken out its control characters.
LABEL = 100
# Nine days of quarter-hours, with room: a longer series is not one of the export's.
MAX_POINTS = 1_000
# The longest period an export covers: nine Paris days, with room for a change of clock.
LONGEST_MS = 10 * 86_400_000
# The name and licence of each source, written here and never read from a source (ADR 029).
SOURCES = {
    "smard": ("Bundesnetzagentur | SMARD.de", "CC BY 4.0"),
    "eco2mix": ("RTE, éCO2mix", "Licence Ouverte 2.0"),
    "openmeteo": ("Weather data by Open-Meteo.com", "CC BY 4.0"),
    "school-holidays": ("Éducation nationale, calendrier scolaire", "Licence Ouverte 2.0"),
    "public-holidays": ("Etalab, jours fériés", "Licence Ouverte 2.0"),
}
# The source and the unit of each series, in the clean layer.
SERIES = {
    "price": ("smard", "EUR/MWh"),
    "consumption": ("eco2mix", "MW"),
    "rte_forecast": ("eco2mix", "MW"),
    "co2": ("eco2mix", "gCO2/kWh"),
    "solar": ("eco2mix", "MW"),
    "temperature": ("openmeteo", "degC"),
    "temperature_forecast": ("openmeteo", "degC"),
}


def printable(text: str) -> str:
    """A name without control or format characters, such as a line break or a change of
    direction: the export takes them out, and the API refuses a name that still has one."""
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in text):
        raise ValueError("a name holds a control or format character")
    return text


# An instant in milliseconds since 1970, between 2000 and 2100.
Millis = Annotated[int, Field(ge=946_684_800_000, lt=4_102_444_800_000)]
Name = Annotated[str, Field(min_length=1, max_length=LABEL), AfterValidator(printable)]


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

    @model_validator(mode="after")
    def as_written_in_the_code(self) -> Self:
        if (self.source, self.unit) != SERIES[self.id]:
            raise ValueError(f"the series {self.id} has another source or unit than the code's")
        return self


class Day(Shape):
    """A Paris day of the period, from its midnight to the next one, with its public holiday and
    its school holidays, if any. Its bounds place the midnights of Paris on the screen, whatever
    the time zone of the browser."""

    day: date
    start: Millis
    end: Millis
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

    @model_validator(mode="after")
    def as_written_in_the_code(self) -> Self:
        if (self.name, self.licence) != SOURCES[self.id]:
            raise ValueError(f"the source {self.id} has another name or licence than the code's")
        return self


class Recent(Shape):
    """The export: the period, from its first instant to its end, excluded, and what it holds."""

    generated_at: Millis
    start: Millis
    end: Millis
    series: Annotated[list[Series], Field(max_length=len(SERIES))]
    days: Annotated[list[Day], Field(max_length=12)]
    sources: Annotated[list[Source], Field(max_length=len(SOURCES))]

    @model_validator(mode="after")
    def within_its_period(self) -> Self:
        if not self.start < self.end <= self.start + LONGEST_MS:
            raise ValueError("the period ends before it starts, or lasts more than ten days")
        series_ids: list[str] = [series.id for series in self.series]
        source_ids: list[str] = [source.id for source in self.sources]
        for names, what in [(series_ids, "series"), (source_ids, "sources")]:
            if len(set(names)) != len(names):
                raise ValueError(f"the {what} are not named once each")
        if self.days:
            starts = [day.start for day in self.days]
            ends = [day.end for day in self.days]
            if (
                (starts[0], ends[-1]) != (self.start, self.end)
                or starts[1:] != ends[:-1]
                or any(end <= start for start, end in zip(starts, ends, strict=True))
            ):
                raise ValueError("the days do not follow each other over the period")
        for series in self.series:
            instants = [instant for instant, _ in series.points]
            if any(later <= earlier for earlier, later in pairwise(instants)):
                raise ValueError(f"the points of {series.id} are not in the order of time")
            if instants and not self.start <= instants[0] <= instants[-1] < self.end:
                raise ValueError(f"the points of {series.id} leave the period")
        return self


# The names of every field of the export: the API names these in its log, and nothing else of a
# file it refuses.
MODELS: tuple[type[Shape], ...] = (Recent, Series, Day, Source)
FIELDS = frozenset(name for model in MODELS for name in model.__pydantic_fields__)
