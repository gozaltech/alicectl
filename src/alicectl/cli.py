import argparse
import asyncio
import logging
import sys
from argparse import ArgumentParser as Parser
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import httpx2
from dotenv import load_dotenv

from . import options as o
from .commands import account, alarms, home, local, setup
from .config import ENV_FILE
from .context import Context
from .errors import AlicectlError
from .repl import network_error, repl, set_debug

OPINIONS = [
    ("like", False, "like the track a station is playing"),
    ("dislike", False, "dislike the playing track and skip it"),
    ("like", True, "remove a like from the playing track"),
    ("dislike", True, "remove a dislike from the playing track"),
]
SHORTCUTS = [
    ("next", o.TrackStepOptions, "skip to the next track, or several"),
    ("prev", o.TrackStepOptions, "go back a track, or several"),
    ("pause", o.PlaybackOptions, "pause playback"),
    ("resume", o.PlaybackOptions, "continue playback"),
    ("forward", o.MoveOptions, "move ahead in the track"),
    ("rewind", o.MoveOptions, "move back in the track"),
    ("seek", o.SeekOptions, "jump to a position in the track"),
]
PLAYER_ACTIONS = {"resume": "play"}


def package_version() -> str:
    try:
        return version("alicectl")
    except PackageNotFoundError:
        return "unknown"


type Registry = Any


def add(sub: Registry, name: str, func: o.Command | None, options: type[o.Options], summary: str, **fixed: Any) -> None:
    parser = sub.add_parser(name, help=summary, description=summary)
    options.add_to(parser)
    parser.set_defaults(func=func, options=options, **fixed)


def add_setup_commands(sub: Registry) -> None:
    add(sub, "login", setup.cmd_login, o.LoginOptions, "get an x_token by confirming a code at ya.ru/device")
    add(sub, "code", setup.cmd_code, o.AuthOptions, "print a fresh xTokenCode")
    add(sub, "sound", setup.cmd_sound, o.SoundOptions, "set up a station by playing the setup tone")
    add(sub, "ble-scan", setup.cmd_ble_scan, o.BleScanOptions, "list stations in setup mode")
    add(sub, "ble", setup.cmd_ble, o.BleOptions, "set up a station over Bluetooth LE")


def add_account_commands(sub: Registry) -> None:
    add(sub, "devices", account.cmd_devices, o.DevicesOptions, "list stations on the account")
    add(sub, "rename", account.cmd_rename, o.RenameOptions, "rename a station")
    add(sub, "remove", account.cmd_remove, o.RemoveOptions, "remove a station from the account")
    add(sub, "settings", account.cmd_settings, o.SettingsOptions, "show or change a station's settings")
    add(sub, "account", account.cmd_account, o.AccountSettingsOptions, "show or change account-wide settings")


def add_station_commands(sub: Registry) -> None:
    add(sub, "say", local.cmd_say, o.TextOptions, "make stations say text")
    add(sub, "ask", local.cmd_ask, o.AskOptions, "send a text command to Alice")
    add(sub, "status", local.cmd_status, o.StationsOptions, "show volume and what stations are playing")
    add(sub, "volume", local.cmd_volume, o.VolumeOptions, "print or set the volume")
    add(sub, "player", local.cmd_player, o.PlayerOptions, "play, pause, skip tracks, seek")
    for name, options, summary in SHORTCUTS:
        add(sub, name, local.cmd_player, options, summary, action=PLAYER_ACTIONS.get(name, name))
    add(sub, "timer", local.cmd_timer, o.TimerOptions, "set or cancel a timer by voice command")


def add_music_commands(sub: Registry) -> None:
    add(sub, "play", local.cmd_play, o.PlayOptions, "play music: a search, your likes, a playlist, My Wave")
    for opinion, undo, summary in OPINIONS:
        name = f"un{opinion}" if undo else opinion
        add(sub, name, local.cmd_opinion, o.OpinionOptions, summary, opinion=opinion, undo=undo)


def add_alarm_commands(sub: Registry) -> None:
    alarm = sub.add_parser("alarm", help="list, add, cancel, enable or disable alarms")
    group = alarm.add_subparsers(dest="alarm_cmd", required=True, metavar="ACTION")
    add(group, "list", alarms.cmd_alarm_list, o.AlarmListOptions, "list alarms")
    add(group, "add", alarms.cmd_alarm_add, o.AlarmAddOptions, "add an alarm")
    add(group, "sound", alarms.cmd_alarm_sound, o.AlarmSoundOptions, "change the sound of an alarm")
    add(group, "defaults", alarms.cmd_alarm_defaults, o.AlarmDefaultsOptions, "a station's alarm sound and volume")
    add(group, "cancel", alarms.cmd_alarm_cancel, o.IdsOptions, "delete alarms")
    for state in ("on", "off"):
        add(group, state, alarms.cmd_alarm_switch, o.AlarmSwitchOptions, f"turn alarms {state}", enabled=state == "on")

    reminder = sub.add_parser("reminder", help="list, add or cancel reminders")
    group = reminder.add_subparsers(dest="reminder_cmd", required=True, metavar="ACTION")
    add(group, "list", alarms.cmd_reminder_list, o.AccountOptions, "list reminders")
    add(group, "add", alarms.cmd_reminder_add, o.ReminderAddOptions, "add a reminder")
    add(group, "cancel", alarms.cmd_reminder_cancel, o.IdsOptions, "delete reminders")


def add_home_commands(sub: Registry) -> None:
    add(sub, "home", home.cmd_home, o.AccountOptions, "list smart-home devices and their state")
    add(sub, "turn", home.cmd_turn, o.TurnOptions, "switch a device on or off")
    add(sub, "set", home.cmd_set, o.SetOptions, "set brightness, temperature, a mode... (see 'home')")
    add(sub, "scenarios", home.cmd_scenarios, o.AccountOptions, "list smart-home scenarios")
    add(sub, "scenario", home.cmd_scenario, o.ScenarioOptions, "run a scenario")


def build_parser() -> Parser:
    parser = Parser(
        prog="alicectl",
        description="Set up and control Yandex Stations and your Alice smart home",
        suggest_on_error=True,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {package_version()}")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    add_setup_commands(sub)
    add_account_commands(sub)
    add_station_commands(sub)
    add_music_commands(sub)
    add_alarm_commands(sub)
    add_home_commands(sub)
    add(sub, "repl", None, o.ReplOptions, "type commands one after another (the default)")
    return parser


def command_names(parser: Parser) -> list[str]:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(sub.choices)


def parse(parser: Parser, argv: list[str]) -> o.Parsed:
    namespace = parser.parse_args(argv)
    options = namespace.options.from_namespace(namespace)
    if problem := options.problem():
        parser.error(problem)
    return o.Parsed(namespace.cmd, namespace.func, options)


async def run(command: o.Parsed) -> None:
    assert command.func is not None
    async with Context(command.options) as ctx:
        await command.func(ctx, command.options)


def main() -> None:
    load_dotenv(ENV_FILE)
    parser = build_parser()
    command = parse(parser, sys.argv[1:] or ["repl"])
    logging.basicConfig(format="%(message)s")
    set_debug(command.options.debug)
    if command.func is None:
        repl(command.options, lambda words: parse(parser, words), command_names(parser))
        return
    try:
        asyncio.run(run(command))
    except AlicectlError as e:
        raise SystemExit(str(e) or 1) from None
    except KeyboardInterrupt:
        raise SystemExit("interrupted") from None
    except httpx2.HTTPError as e:
        if command.options.debug:
            raise
        raise SystemExit(network_error(e)) from None
