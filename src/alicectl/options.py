import argparse
import datetime as dt
import os
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import MISSING, Field, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Self, get_type_hints

from .api.auth import DEVICE_NAME
from .api.music import KINDS
from .catalog import ACCOUNT_SETTINGS, STATION_SETTINGS, SWITCH, Setting
from .config import TOKEN_FILE
from .control.actions import PLAYER, REPEAT_MODES, SEEK_STEP, SEEKS
from .errors import AlicectlError
from .schedule import DAY_PRESETS, clock_arg, date_arg, days_arg, timer_arg
from .setup.payload import SECURITY_CHOICES, wifi_error

if TYPE_CHECKING:
    from .context import Context

DEFAULT_STATION = "ALICECTL_STATION"


@dataclass(frozen=True)
class Arg:
    flags: tuple[str, ...]
    settings: dict[str, Any]
    group: str = ""
    env: str = ""
    consts: dict[str, tuple[Any, str]] = field(default_factory=dict)


def arg(
    *flags: str, group: str = "", env: str = "", consts: dict[str, tuple[Any, str]] | None = None, **settings: Any
) -> Arg:
    return Arg(flags, settings, group, env, consts or {})


def chosen_station(value: str | None) -> str:
    if chosen := value or os.environ.get(DEFAULT_STATION):
        return chosen
    raise AlicectlError(f"which station? add -s NAME, or set {DEFAULT_STATION} in .env (or 'use NAME' in the REPL)")


@dataclass(kw_only=True)
class Options:
    debug: Annotated[bool, arg("--debug", action="store_true", help="show HTTP and protocol details")] = False

    @classmethod
    def arguments(cls) -> Iterator[tuple[Field, Arg]]:
        hints = get_type_hints(cls, include_extras=True)
        for f in fields(cls):
            for meta in getattr(hints[f.name], "__metadata__", ()):
                if isinstance(meta, Arg):
                    yield f, meta

    @classmethod
    def add_to(cls, parser: argparse.ArgumentParser) -> None:
        groups: dict[str, Any] = {}
        for f, meta in cls.arguments():
            if meta.group and meta.group not in groups:
                groups[meta.group] = parser.add_mutually_exclusive_group()
            target = groups[meta.group] if meta.group else parser
            settings = dict(meta.settings)
            default = os.environ.get(meta.env, f.default) if meta.env else f.default
            if default is not MISSING and settings.get("action") not in {"store_true", "store_false"}:
                settings.setdefault("default", default)
            if meta.flags:
                target.add_argument(*meta.flags, dest=f.name, **settings)
            elif not meta.consts:
                target.add_argument(f.name, **settings)
            for flag, (const, help_text) in meta.consts.items():
                target.add_argument(flag, dest=f.name, action="store_const", const=const, help=help_text)

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> Self:
        return cls(**{f.name: getattr(namespace, f.name) for f in fields(cls) if f.init and hasattr(namespace, f.name)})

    def problem(self) -> str | None:
        return None


type Command = Callable[[Context, Any], Awaitable[None]]


@dataclass(frozen=True)
class Parsed:
    name: str
    func: Command | None
    options: Options


@dataclass(kw_only=True)
class AccountOptions(Options):
    x_token: Annotated[
        str | None, arg("--x-token", help=f"master token (default: from {TOKEN_FILE.name}, device login if needed)")
    ] = None


@dataclass(kw_only=True)
class OAuthOptions:
    oauth_token: Annotated[
        str | None,
        arg("--oauth-token", env="YANDEX_OAUTH_TOKEN", help="OAuth token (env YANDEX_OAUTH_TOKEN; default: derived)"),
    ] = None


@dataclass(kw_only=True)
class AuthOptions(AccountOptions):
    code: Annotated[str | None, arg("--code", help="ready xTokenCode (single-use, expires fast)")] = None


@dataclass(kw_only=True)
class StationsOptions(AccountOptions, OAuthOptions):
    station: Annotated[
        str | None,
        arg(
            "-s",
            "--station",
            help=f"station name or ID, several separated by commas, or 'all' (default: {DEFAULT_STATION})",
        ),
    ] = None

    @property
    def stations(self) -> str:
        return chosen_station(self.station)


@dataclass(kw_only=True)
class StationOptions(AccountOptions):
    station: Annotated[str | None, arg("-s", "--station", help=f"station name or ID (default: {DEFAULT_STATION})")] = (
        None
    )

    @property
    def chosen(self) -> str:
        return chosen_station(self.station)


@dataclass(kw_only=True)
class NamedStationOptions(AccountOptions):
    station: Annotated[str, arg("-s", "--station", required=True, help="station name or ID (see 'devices')")]


@dataclass(kw_only=True)
class QuietOptions:
    quiet: Annotated[bool, arg("--quiet", action="store_true", help="mute Alice's answer (local network only)")] = False


@dataclass(kw_only=True)
class WifiOptions:
    ssid: Annotated[str, arg("--ssid", env="WIFI_SSID", help="Wi-Fi name (env WIFI_SSID)")] = ""
    password: Annotated[str, arg("--password", env="WIFI_PASSWORD", help="Wi-Fi password (env WIFI_PASSWORD)")] = ""
    security: Annotated[str, arg("--security", choices=SECURITY_CHOICES, help="none = account binding only")] = "wpa"
    hidden: Annotated[
        bool, arg("--hidden", action="store_true", help="hidden SSID: send the full SSID and type instead of a hash")
    ] = False

    def problem(self) -> str | None:
        return wifi_error(self.ssid, self.password, self.security)


@dataclass(kw_only=True)
class PlacementOptions:
    name: Annotated[str | None, arg("--name", help="after setup: rename the station")] = None
    room: Annotated[str | None, arg("--room", help="after setup: move the station to this room")] = None
    account_timeout: Annotated[
        float, arg("--account-timeout", type=float, help="seconds to wait for it in the account")
    ] = 300


@dataclass(kw_only=True)
class ScanOptions:
    scan_timeout: Annotated[float, arg("--scan-timeout", type=float, help="seconds per BLE scan")] = 8


@dataclass(kw_only=True)
class SoundChoiceOptions:
    sound: Annotated[
        str | None, arg("--sound", group="sound", metavar="SEARCH", help="wake with a track from Yandex Music")
    ] = None
    melody: Annotated[
        str | None, arg("--melody", group="sound", help="wake with a built-in melody (see 'alarm defaults')")
    ] = None


@dataclass(kw_only=True)
class ScheduleOptions:
    date: Annotated[
        dt.date | None, arg("--date", group="when", type=date_arg, help="one day, YYYY-MM-DD (default: the next one)")
    ] = None
    days: Annotated[
        tuple[str, ...] | None,
        arg(
            "--days",
            group="when",
            type=days_arg,
            help="repeat on days: mon,wed,fri / пн,ср,пт / daily / weekdays",
            consts={f"--{preset}": (tuple(days), f"repeat {preset}") for preset, days in DAY_PRESETS.items()},
        ),
    ] = None


@dataclass(kw_only=True)
class LoginOptions(Options):
    device_name: Annotated[str, arg("--device-name", help="name shown on the confirmation page")] = DEVICE_NAME


@dataclass(kw_only=True)
class SoundOptions(AuthOptions, WifiOptions, PlacementOptions):
    random_code: Annotated[bool, arg("--random-code", action="store_true", help="Wi-Fi only, no account binding")] = (
        False
    )
    wav: Annotated[Path | None, arg("--wav", type=Path, help="where to save the WAV")] = None
    no_play: Annotated[bool, arg("--no-play", action="store_true", help="only write the WAV")] = False
    volume: Annotated[float, arg("--volume", type=float, help="0..1")] = 1.0
    repeat: Annotated[int, arg("--repeat", type=int, help="how many times to play it")] = 1

    def problem(self) -> str | None:
        if self.random_code and (self.name or self.room):
            return "--name/--room need account binding, which --random-code skips"
        return WifiOptions.problem(self)


@dataclass(kw_only=True)
class BleScanOptions(Options, ScanOptions):
    pass


@dataclass(kw_only=True)
class BleOptions(AuthOptions, OAuthOptions, WifiOptions, ScanOptions, PlacementOptions):
    address: Annotated[str | None, arg("--address", help="BLE address (default: strongest station found)")] = None
    platform: Annotated[str | None, arg("--platform", help="override model, e.g. yandexstation_2")] = None
    find_timeout: Annotated[
        float, arg("--find-timeout", type=float, help="seconds to wait for a station to appear (0 = forever)")
    ] = 300
    retries: Annotated[int, arg("--retries", type=int, help="reconnect attempts before credentials are written")] = 3
    wait: Annotated[float, arg("--wait", type=float, help="seconds to wait for the next station status")] = 180

    def problem(self) -> str | None:
        return WifiOptions.problem(self)


@dataclass(kw_only=True)
class DevicesOptions(AccountOptions, OAuthOptions):
    all: Annotated[bool, arg("--all", action="store_true", help="include phone apps")] = False


@dataclass(kw_only=True)
class RenameOptions(NamedStationOptions):
    name: Annotated[str, arg(metavar="NAME", help="new name")]


@dataclass(kw_only=True)
class RemoveOptions(NamedStationOptions):
    yes: Annotated[bool, arg("--yes", action="store_true", help="don't ask for confirmation")] = False


def requested(namespace: argparse.Namespace, settings: tuple[Setting, ...]) -> list[tuple[Setting, Any]]:
    return [(s, value) for s in settings if (value := getattr(namespace, s.dest, None)) is not None]


@dataclass(kw_only=True)
class SettingsOptions(StationOptions):
    changes: list[tuple[Setting, Any]] = field(default_factory=list)

    @classmethod
    def add_to(cls, parser: argparse.ArgumentParser) -> None:
        super().add_to(parser)
        for setting in STATION_SETTINGS:
            setting.add_option(parser, station=True)

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> Self:
        options = super().from_namespace(namespace)
        options.changes = requested(namespace, STATION_SETTINGS)
        return options


@dataclass(kw_only=True)
class AccountSettingsOptions(AccountOptions):
    raw: Annotated[bool, arg("--raw", action="store_true", help="print the whole account config")] = False
    assignments: Annotated[
        list[str] | None,
        arg("--set", action="append", metavar="KEY=VALUE", help="change any key, e.g. jingle=false (expert)"),
    ] = None
    yes: Annotated[bool, arg("--yes", action="store_true", help="don't ask for confirmation")] = False
    changes: list[tuple[Setting, Any]] = field(default_factory=list)

    @classmethod
    def add_to(cls, parser: argparse.ArgumentParser) -> None:
        super().add_to(parser)
        for setting in ACCOUNT_SETTINGS:
            setting.add_option(parser, station=False)

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> Self:
        options = super().from_namespace(namespace)
        options.changes = requested(namespace, ACCOUNT_SETTINGS)
        return options


@dataclass(kw_only=True)
class TextOptions(StationsOptions):
    text: Annotated[list[str], arg(nargs="+", help="what to say or ask")]

    @property
    def joined(self) -> str:
        return " ".join(self.text)


@dataclass(kw_only=True)
class AskOptions(TextOptions, QuietOptions):
    pass


@dataclass(kw_only=True)
class VolumeOptions(StationsOptions):
    level: Annotated[
        int | None, arg(nargs="?", type=int, choices=range(11), metavar="LEVEL", help="0-10; omit to print it")
    ] = None


@dataclass(kw_only=True)
class PlayerOptions(StationsOptions):
    action: Annotated[str, arg(choices=[*PLAYER, *SEEKS])]
    amount: Annotated[
        str | None,
        arg(
            nargs="?",
            help=f"next/prev: how many tracks (1); forward/rewind: how far ({SEEK_STEP.seconds}s); "
            "seek: where to, e.g. 1:30 or 90 (seconds)",
        ),
    ] = None


@dataclass(kw_only=True)
class PlaybackOptions(StationsOptions):
    action: str = ""
    amount: str | None = None


@dataclass(kw_only=True)
class TrackStepOptions(PlaybackOptions):
    amount: Annotated[str | None, arg(nargs="?", metavar="COUNT", help="how many tracks (default 1)")] = None


@dataclass(kw_only=True)
class MoveOptions(PlaybackOptions):
    amount: Annotated[
        str | None, arg(nargs="?", metavar="SPAN", help=f"how far, e.g. 30, 1m, 1m30s (default {SEEK_STEP.seconds}s)")
    ] = None


@dataclass(kw_only=True)
class SeekOptions(PlaybackOptions):
    amount: Annotated[str | None, arg(metavar="POSITION", help="where to, e.g. 1:30 or 90 (seconds)")] = None


@dataclass(kw_only=True)
class TimerOptions(StationsOptions, QuietOptions):
    duration: Annotated[
        dt.timedelta | None, arg(type=timer_arg, metavar="DURATION|cancel", help="minutes, or e.g. 1h30m, 90s")
    ]


@dataclass(kw_only=True)
class PlayOptions(StationsOptions):
    query: Annotated[list[str], arg(nargs="*", help="what to search on Yandex Music (best match)")] = field(
        default_factory=list
    )
    kind: Annotated[
        str | None,
        arg(group="source", consts={f"--{kind}": (kind, f"play the best {kind}") for kind in KINDS}),
    ] = None
    likes: Annotated[bool, arg("--likes", group="source", action="store_true", help="your «Мне нравится»")] = False
    my_playlist: Annotated[
        str | None, arg("--my-playlist", group="source", metavar="NAME", help="one of your own playlists")
    ] = None
    wave: Annotated[bool, arg("--wave", group="source", action="store_true", help="«Моя волна»")] = False
    shuffle: Annotated[bool, arg("--shuffle", action="store_true", help="shuffle")] = False
    repeat: Annotated[str | None, arg("--repeat", choices=list(REPEAT_MODES), help="repeat one track or all")] = None


@dataclass(kw_only=True)
class OpinionOptions(StationsOptions):
    opinion: str = "like"
    undo: bool = False


@dataclass(kw_only=True)
class AlarmListOptions(AccountOptions):
    station: Annotated[
        str | None, arg("-s", "--station", help="station names or IDs separated by commas (default: all)")
    ] = None
    all: Annotated[bool, arg("--all", action="store_true", help="include past one-time alarms")] = False


@dataclass(kw_only=True)
class AlarmAddOptions(StationOptions, SoundChoiceOptions, ScheduleOptions):
    time: Annotated[dt.time, arg(type=clock_arg, metavar="HH:MM", help="when it rings")]
    name: Annotated[str | None, arg("--name", help="alarm name")] = None


@dataclass(kw_only=True)
class AlarmSoundOptions(AccountOptions, SoundChoiceOptions):
    id: Annotated[str, arg(help="alarm ID (see 'alarm list')")]


@dataclass(kw_only=True)
class AlarmDefaultsOptions(StationOptions, SoundChoiceOptions):
    volume: Annotated[int | None, arg("--volume", type=int, choices=range(11), metavar="0-10", help="alarm volume")] = (
        None
    )
    gradual: Annotated[str | None, arg("--gradual", choices=list(SWITCH), help="start quiet and get louder")] = None


@dataclass(kw_only=True)
class IdsOptions(AccountOptions):
    ids: Annotated[list[str], arg(nargs="+", help="IDs (see the list command)")]


@dataclass(kw_only=True)
class AlarmSwitchOptions(IdsOptions):
    enabled: bool = True


@dataclass(kw_only=True)
class ReminderAddOptions(AccountOptions, ScheduleOptions):
    text: Annotated[list[str], arg(nargs="+", help="what to remind about")]
    time: Annotated[dt.time, arg("--at", type=clock_arg, required=True, metavar="HH:MM", help="when")]
    station: Annotated[
        str | None, arg("-s", "--station", help="stations that say it: names or IDs separated by commas, or 'all'")
    ] = None
    push: Annotated[bool, arg("--no-push", action="store_false", help="no phone notification")] = True


@dataclass(kw_only=True)
class DeviceOptions(AccountOptions):
    device: Annotated[str, arg(help="device name or ID (see 'home')")]


@dataclass(kw_only=True)
class TurnOptions(DeviceOptions):
    state: Annotated[str, arg(choices=["on", "off"], help="on or off")]


@dataclass(kw_only=True)
class SetOptions(DeviceOptions):
    what: Annotated[str, arg(help="e.g. brightness, temperature, fan_speed")]
    value: Annotated[str, arg(help="the new value")]


@dataclass(kw_only=True)
class ScenarioOptions(AccountOptions):
    scenario: Annotated[str, arg(help="scenario name or ID")]


@dataclass(kw_only=True)
class ReplOptions(AccountOptions, OAuthOptions):
    pass
