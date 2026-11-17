import asyncio
import copy
import json
import logging
from typing import Any

from ..api.iot import AccountConfig
from ..catalog import ACCOUNT_SETTINGS, INHERIT, STATION_SETTINGS, Setting, del_path, get_path, set_path
from ..context import Context
from ..errors import AlicectlError
from ..options import AccountSettingsOptions, DevicesOptions, RemoveOptions, RenameOptions, SettingsOptions
from .base import confirm, one_station, table

log = logging.getLogger(__name__)

HIDDEN_ACCOUNT_KEYS = {"user_wifi_config"}


async def firmware_versions(ctx: Context) -> dict[str, str]:
    try:
        stations = await (await ctx.home()).stations()
    except AlicectlError as e:
        log.warning(f"firmware versions unavailable: {e}")
        return {}
    return {d.quasar_id: d.firmware for d in stations}


async def cmd_devices(ctx: Context, args: DevicesOptions) -> None:
    firmware_task = asyncio.create_task(firmware_versions(ctx))
    listed = await ctx.devices(refresh=True)
    firmware = await firmware_task
    if not (devices := [d for d in listed if args.all or not d.is_app]):
        print("no devices on this account")
        return
    rows = [("NAME", "PLATFORM", "ID", "FIRMWARE", "WI-FI", "IP", "UPDATED")]
    rows += [
        (
            d.name,
            d.platform,
            d.id,
            firmware.get(d.id, ""),
            d.wifi,
            d.ip,
            f"{d.updated.astimezone():%Y-%m-%d %H:%M}" if d.updated else "",
        )
        for d in devices
    ]
    table(rows)


async def cmd_rename(ctx: Context, args: RenameOptions) -> None:
    home = await ctx.home()
    device = await one_station(home, args.station)
    await home.rename(device, args.name)
    log.info(f"renamed {device.name!r} to {args.name!r}")


async def cmd_remove(ctx: Context, args: RemoveOptions) -> None:
    home = await ctx.home()
    device = await one_station(home, args.station)
    confirm(
        f"Remove {device.name!r} ({device.quasar_id}) from your account? It will need setting up again.",
        args.yes,
    )
    await home.remove(device)
    log.info(f"removed {device.name!r} from the account")


def raw_assignment(text: str) -> tuple[tuple[str, ...], Any]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise AlicectlError(f"--set expects KEY=VALUE, got {text!r}")
    try:
        parsed = json.loads(value)
    except ValueError:
        parsed = value
    return tuple(key.split(".")), parsed


def account_changes(before: AccountConfig, after: AccountConfig, raw: list[tuple[tuple[str, ...], Any]]) -> list[str]:
    lines = [
        f"{s.id}: {s.show(s.get_account(before))} → {s.show(s.get_account(after))}"
        for s in ACCOUNT_SETTINGS
        if s.show(s.get_account(before)) != s.show(s.get_account(after))
    ]
    lines += [
        f"{'.'.join(path)}: {json.dumps(get_path(before.config, path), ensure_ascii=False)} → "
        f"{json.dumps(value, ensure_ascii=False)}"
        for path, value in raw
        if get_path(before.config, path) != value
    ]
    return lines


async def cmd_account(ctx: Context, args: AccountSettingsOptions) -> None:
    home = await ctx.home()
    account = await home.account_config()
    changes, raw = args.changes, [raw_assignment(item) for item in args.assignments or []]
    if changes or raw:
        updated = copy.deepcopy(account)
        for setting, value in changes:
            setting.apply_account(updated, value)
        for path, value in raw:
            set_path(updated.config, path, value)
        if lines := account_changes(account, updated, raw):
            print("\n".join(lines))
            confirm("Apply to the whole account (every station)?", args.yes)
            await home.set_account_config(updated)
            account = await home.account_config()
        else:
            log.info("nothing to change")
    if args.raw:
        shown = {k: ("<hidden>" if k in HIDDEN_ACCOUNT_KEYS else v) for k, v in account.config.items()}
        print(json.dumps(shown, ensure_ascii=False, indent=2))
    else:
        table([(f"{s.id}:", s.show(s.get_account(account)), s.help) for s in ACCOUNT_SETTINGS])


def station_value(setting: Setting, config: dict, account: AccountConfig) -> str:
    if not setting.per_station:
        return setting.show(setting.get(config))
    if get_path(config, setting.path) is not None:
        return f"{setting.show(setting.get(config))} (this station)"
    return f"{setting.show(setting.get_account(account))} (from the account)"


def apply_station(setting: Setting, config: dict, value: Any, account: AccountConfig) -> None:
    values = value if setting.repeatable else [value]
    if INHERIT in values:
        del_path(config, setting.path)
        return
    inherited = setting.get_account(account)
    if setting.per_station and get_path(config, setting.path) is None and inherited is not None:
        set_path(config, setting.path, copy.deepcopy(inherited))
    setting.apply(config, value)


async def cmd_settings(ctx: Context, args: SettingsOptions) -> None:
    home = await ctx.home()
    device = await one_station(home, args.chosen)
    config, version = await home.station_config(device)
    account = await home.account_config()
    updated = copy.deepcopy(config)
    for setting, value in args.changes:
        apply_station(setting, updated, value, account)
    if updated != config:
        await home.set_station_config(device, updated, version)
        config, _ = await home.station_config(device)
        log.info(f"{device.name}: settings updated")
    table([(f"{s.id}:", station_value(s, config, account)) for s in STATION_SETTINGS])
