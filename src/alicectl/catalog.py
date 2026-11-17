import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .api.iot import AccountConfig

INHERIT = "account"
SWITCH = {"on": True, "off": False}
WAKE_WORDS = ["alisa", "yandex"]
CONTENT_LEVELS = ["safe", "children", "medium", "without"]
MUSIC_QUALITY = ["low", "normal", "high", "auto"]
COMMAND_SPOTTER_GROUPS = {
    "music": ["playAndPause", "navigation", "feedback", "volume", "bluetooth"],
    "call": ["answer"],
    "tv": ["navigation", "backToHome"],
    "smartHome": ["light", "tv"],
}
DND_DEFAULT_HOURS = ("23:00", "09:00")
HOURS = re.compile(r"((?:[01]?\d|2[0-3]):[0-5]\d)-((?:[01]?\d|2[0-3]):[0-5]\d)")

type Path = tuple[str, ...]


def get_path(data: dict, path: Path) -> Any:
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def set_path(data: dict, path: Path, value: Any) -> None:
    *parents, last = path
    for key in parents:
        if not isinstance(data.get(key), dict):
            data[key] = {}
        data = data[key]
    data[last] = value


def del_path(data: dict, path: Path) -> bool:
    *parents, last = path
    parent = get_path(data, tuple(parents)) if parents else data
    return isinstance(parent, dict) and parent.pop(last, None) is not None


def on_off(value: Any) -> str:
    return "on" if value else "off"


@dataclass(frozen=True, kw_only=True)
class Setting:
    id: str
    path: Path
    help: str
    aliases: tuple[str, ...] = ()
    default: Any = None
    per_station: bool = False
    repeatable: bool = False
    metavar: str | None = None
    choices: tuple[str, ...] | None = None

    @property
    def dest(self) -> str:
        return self.id.replace("-", "_")

    def parse(self, text: str) -> Any:
        if self.choices is not None and text not in self.choices:
            raise argparse.ArgumentTypeError(f"expected one of: {', '.join(self.choices)}")
        return text

    def get(self, config: dict) -> Any:
        value = get_path(config, self.path)
        return self.default if value is None else value

    def apply(self, config: dict, value: Any) -> None:
        set_path(config, self.path, value)

    def show(self, value: Any) -> str:
        return "default" if value is None else str(value)

    def get_account(self, account: AccountConfig) -> Any:
        return self.get(account.config)

    def apply_account(self, account: AccountConfig, value: Any) -> None:
        self.apply(account.config, value)

    def add_option(self, parser: argparse.ArgumentParser, station: bool) -> None:
        choices = [*self.choices, INHERIT] if station and self.per_station and self.choices else self.choices
        parser.add_argument(
            *(f"--{name}" for name in (self.id, *self.aliases)),
            dest=self.dest,
            type=self.parse_station if station and self.per_station else self.parse,
            action="append" if self.repeatable else "store",
            metavar=self.metavar or (f"{{{','.join(choices)}}}" if choices else None),
            help=self.help + (f"; '{INHERIT}' uses the account's again" if station and self.per_station else ""),
        )

    def parse_station(self, text: str) -> Any:
        return text if text == INHERIT else self.parse(text)


class Switch(Setting):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(choices=tuple(SWITCH), **kwargs)

    def parse(self, text: str) -> Any:
        return SWITCH[super().parse(text)]

    def show(self, value: Any) -> str:
        return on_off(value)


class Spotter(Setting):
    def apply_account(self, account: AccountConfig, value: Any) -> None:
        self.apply(account.config, value)
        account.localization.setdefault("ru-RU", {})["spotter"] = value

    def get_account(self, account: AccountConfig) -> Any:
        return (account.localization.get("ru-RU") or {}).get("spotter") or self.get(account.config)


class CommandSpotters(Setting):
    def parse(self, text: str) -> Any:
        match text.split("="):
            case [name, state] if state in SWITCH and self.known(name):
                return name, SWITCH[state]
        names = ", ".join(f"{group}.{item}" for group, items in COMMAND_SPOTTER_GROUPS.items() for item in items)
        raise argparse.ArgumentTypeError(f"expected GROUP.COMMAND=on|off, one of: {names}")

    @staticmethod
    def known(name: str) -> bool:
        group, _, item = name.partition(".")
        return item in COMMAND_SPOTTER_GROUPS.get(group, [])

    def apply(self, config: dict, value: Any) -> None:
        for name, state in value:
            set_path(config, (*self.path, *name.split(".")), state)

    def show(self, value: Any) -> str:
        enabled = [
            f"{group}.{item}" for group, items in (value or {}).items() for item, state in items.items() if state
        ]
        return ", ".join(enabled) or "none"


class BetaTesting(Switch):
    def apply(self, config: dict, value: Any) -> None:
        super().apply(config, value)
        if not value and config.get("tv_beta"):
            config["tv_beta"] = False


class Brightness(Setting):
    def parse(self, text: str) -> Any:
        if text == "auto" or (text.isdigit() and 0 <= int(text) <= 100):
            return text
        raise argparse.ArgumentTypeError("expected auto or 0-100")

    def apply(self, config: dict, value: Any) -> None:
        current = self.get(config) or {}
        auto = value == "auto"
        set_path(config, self.path, {"auto": auto, "value": current.get("value", 0.5) if auto else int(value) / 100})

    def show(self, value: Any) -> str:
        match value:
            case {"auto": True}:
                return "auto"
            case {"value": float() | int() as level}:
                return f"{round(level * 100)}%"
        return "not reported by this model"


class DoNotDisturb(Setting):
    def parse(self, text: str) -> Any:
        if text in SWITCH or HOURS.fullmatch(text):
            return text
        raise argparse.ArgumentTypeError("expected on, off or hours like 23:00-08:00")

    def apply(self, config: dict, value: Any) -> None:
        dnd = dict(self.get(config) or {})
        if match := HOURS.fullmatch(value):
            hours, dnd["enabled"] = (match[1], match[2]), True
        else:
            hours, dnd["enabled"] = (None if dnd.get("starts") else DND_DEFAULT_HOURS), SWITCH[value]
        if hours:
            dnd["starts"], dnd["ends"] = (f"{hour.zfill(5)}:00{datetime.now().astimezone():%z}" for hour in hours)
        set_path(config, self.path, dnd)

    def show(self, value: Any) -> str:
        dnd = value or {}
        hours = f", {dnd['starts'][:5]}-{dnd['ends'][:5]}" if dnd.get("starts") and dnd.get("ends") else ""
        return f"{on_off(dnd.get('enabled'))}{hours}"


SPOTTER = Spotter(
    id="spotter",
    aliases=("wake-word",),
    path=("spotter",),
    choices=tuple(WAKE_WORDS),
    per_station=True,
    help="wake word",
)
COMMAND_SPOTTER = CommandSpotters(
    id="command-spotter",
    aliases=("commands-without-wake-word",),
    path=("enabledCommandSpotters",),
    per_station=True,
    repeatable=True,
    metavar="GROUP.COMMAND=on|off",
    help="commands that work without the wake word, e.g. music.volume=on",
)

ACCOUNT_SETTINGS: tuple[Setting, ...] = (
    SPOTTER,
    Switch(
        id="jingle",
        aliases=("activation-sound",),
        path=("jingle",),
        default=False,
        help="sound when Alice starts listening",
    ),
    Switch(
        id="adaptive-volume",
        path=("aliceAdaptiveVolume", "enabled"),
        default=True,
        help="Alice adjusts her loudness to the surroundings",
    ),
    Switch(
        id="alice-proactivity",
        aliases=("proactivity",),
        path=("aliceProactivity",),
        default=True,
        help="Alice suggests things on her own",
    ),
    COMMAND_SPOTTER,
    Switch(
        id="crossfade",
        path=("audio_player", "crossfadeEnabled"),
        default=False,
        help="smooth transitions between tracks",
    ),
    Switch(id="smart-activation", path=("smartActivation",), default=True, help="smart activation"),
    Switch(
        id="always-on-mic", path=("alwaysOnMicForShortcuts",), default=False, help="microphone always on for shortcuts"
    ),
    Setting(
        id="content-access",
        path=("contentAccess",),
        choices=tuple(CONTENT_LEVELS),
        help="what Alice may play or say to adults",
    ),
    Setting(
        id="kids-content-access",
        path=("childContentAccess",),
        choices=tuple(CONTENT_LEVELS),
        help="what Alice may play or say to children",
    ),
    Switch(id="child-voice", path=("useBiometryChildScoring",), default=True, help="recognise children by their voice"),
)

STATION_SETTINGS: tuple[Setting, ...] = (
    SPOTTER,
    COMMAND_SPOTTER,
    DoNotDisturb(id="dnd", path=("dndMode",), metavar="on|off|HH:MM-HH:MM", help="do not disturb"),
    Brightness(
        id="led-brightness",
        aliases=("brightness",),
        path=("led", "brightness"),
        metavar="auto|0-100",
        help="screen or clock brightness",
    ),
    Switch(
        id="vad",
        aliases=("voice-activation",),
        path=("voice_activation", "enabled"),
        default=False,
        help="react to the wake word (models where it can be switched off)",
    ),
    Setting(
        id="music-quality",
        path=("audio_player", "music_quality"),
        choices=tuple(MUSIC_QUALITY),
        help="streaming quality",
    ),
    BetaTesting(id="beta-testing", aliases=("beta",), path=("beta",), default=False, help="beta firmware"),
    Setting(
        id="bluetooth",
        path=("bluetooth_state",),
        choices=tuple(SWITCH),
        default="off",
        help="discoverable over Bluetooth",
    ),
)
