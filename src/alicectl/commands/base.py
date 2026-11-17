import sys
from collections.abc import Sequence

from ..api.iot import IotDevice, SmartHome
from ..errors import AlicectlError
from ..lookup import pick, pick_many


def table(rows: Sequence[Sequence[str]]) -> None:
    widths = [max(map(len, column)) for column in zip(*rows, strict=True)]
    for row in rows:
        print("  ".join(f"{cell:<{width}}" for cell, width in zip(row, widths, strict=True)).rstrip())


def confirm(question: str, assume_yes: bool = False) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise AlicectlError("no terminal to confirm in; pass --yes")
    try:
        answer = input(f"{question} [y/N] ").strip().casefold()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        raise AlicectlError("cancelled")


def device_keys(device: IotDevice) -> tuple[str, str, str]:
    return device.id, device.quasar_id, device.name


async def one_device(home: SmartHome, query: str) -> IotDevice:
    return pick(await home.devices(), query, device_keys, "device")


async def one_station(home: SmartHome, query: str) -> IotDevice:
    return pick(await home.stations(), query, device_keys, "station")


async def some_stations(home: SmartHome, query: str | None) -> list[IotDevice]:
    return pick_many(await home.stations(), query, device_keys, "station")
