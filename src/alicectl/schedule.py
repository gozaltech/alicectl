import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_NAMES = {
    **{day: day for day in DAYS},
    **{day[:3]: day for day in DAYS},
    **dict(zip(["пн", "вт", "ср", "чт", "пт", "сб", "вс"], DAYS, strict=True)),
}
DAY_PRESETS = {"daily": DAYS, "weekdays": DAYS[:5], "weekends": DAYS[5:]}
CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
TIMESTAMP = re.compile(r"(?:(\d+):)?(\d+):([0-5]\d)")
CANCEL_WORDS = {"cancel", "stop", "отмена"}


def days_arg(text: str) -> tuple[str, ...]:
    text = text.strip().casefold()
    if text in DAY_PRESETS:
        return tuple(DAY_PRESETS[text])
    names = [DAY_NAMES.get(part.strip()) for part in text.split(",")]
    if None in names:
        raise argparse.ArgumentTypeError("expected days like mon,wed,fri (or пн,ср,пт), daily, weekdays or weekends")
    return tuple(sorted({name for name in names if name}, key=DAYS.index))


def clock_arg(text: str) -> time:
    if not (match := CLOCK.fullmatch(text.strip())):
        raise argparse.ArgumentTypeError("expected HH:MM")
    return time(int(match[1]), int(match[2]))


def date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from None


def parse_span(text: str, bare_unit: str, timestamps: bool = False) -> timedelta | None:
    text = text.strip().casefold()
    match = TIMESTAMP.fullmatch(text) if timestamps else None
    match = match or DURATION.fullmatch(text + bare_unit if text.isdigit() else text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(group or 0) for group in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def duration_arg(text: str) -> timedelta:
    if (span := parse_span(text, "m")) is None:
        raise argparse.ArgumentTypeError("expected minutes or e.g. 1h30m, 90s")
    if not span:
        raise argparse.ArgumentTypeError("the duration must be longer than zero")
    return span


def position_arg(text: str) -> timedelta:
    if (span := parse_span(text, "s", timestamps=True)) is None:
        raise argparse.ArgumentTypeError("expected seconds or e.g. 1m30s, 1:30")
    return span


def step_arg(text: str) -> timedelta:
    if not (span := position_arg(text)):
        raise argparse.ArgumentTypeError("the step must be longer than zero")
    return span


def count_arg(text: str) -> int:
    if not text.strip().isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("expected a number of tracks, 1 or more")
    return int(text)


def describe_position(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def timer_arg(text: str) -> timedelta | None:
    return None if text.strip().casefold() in CANCEL_WORDS else duration_arg(text)


def describe_days(days: tuple[str, ...]) -> str:
    for preset, preset_days in DAY_PRESETS.items():
        if list(days) == preset_days:
            return preset
    return ",".join(day[:3] for day in days)


@dataclass(slots=True, frozen=True)
class Schedule:
    clock: time
    on: date | None = None
    days: tuple[str, ...] = ()

    @property
    def moment(self) -> datetime:
        if self.on:
            return datetime.combine(self.on, self.clock)
        now = datetime.now()
        today = datetime.combine(now.date(), self.clock)
        return today if today > now else today + timedelta(days=1)

    def __str__(self) -> str:
        return f"{describe_days(self.days)} {self.clock:%H:%M}" if self.days else f"{self.moment:%Y-%m-%d %H:%M}"
