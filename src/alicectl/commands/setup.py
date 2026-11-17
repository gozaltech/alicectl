import asyncio
import logging
import secrets
import tempfile
import time
from pathlib import Path

from ..api.auth import x_token_by_device
from ..config import save_token
from ..context import Context
from ..errors import AlicectlError
from ..lookup import pick
from ..options import AuthOptions, BleOptions, BleScanOptions, LoginOptions, PlacementOptions, SoundOptions
from ..setup.payload import config_payload
from ..setup.sound import play_wav, tones, write_wav

log = logging.getLogger(__name__)

ACCOUNT_POLL_SECONDS = 5


async def cmd_login(ctx: Context, args: LoginOptions) -> None:
    save_token(await x_token_by_device(args.device_name))


async def cmd_code(ctx: Context, args: AuthOptions) -> None:
    print(await ctx.code())


async def account_snapshot(ctx: Context, args: PlacementOptions) -> set[str] | None:
    if not (args.name or args.room):
        return None
    devices, rooms = await (await ctx.home()).overview()
    if args.room and not any(args.room.casefold() in {r.id.casefold(), r.name.casefold()} for r in rooms):
        raise AlicectlError(f"no room named {args.room!r} on the account")
    return {d.quasar_id for d in devices if d.quasar_id}


async def place_station(
    ctx: Context, args: PlacementOptions, before: set[str] | None, device_id: str | None = None
) -> None:
    if before is None:
        return
    log.info(f"waiting up to {args.account_timeout:g}s for the station to appear in your account ...")
    deadline = time.monotonic() + args.account_timeout
    home = await ctx.home()
    while True:
        devices, rooms = await home.overview()
        new = [
            d for d in devices if d.quasar_id and (d.quasar_id == device_id if device_id else d.quasar_id not in before)
        ]
        match new:
            case [station]:
                break
            case [_, _, *_]:
                raise AlicectlError("several new stations appeared; name them with 'rename' instead")
        if time.monotonic() > deadline:
            log.warning("the station didn't appear in your account; set name and room later")
            return
        await asyncio.sleep(ACCOUNT_POLL_SECONDS)
    if args.room:
        household_rooms = [r for r in rooms if r.household_id == station.household_id]
        room = pick(household_rooms, args.room, lambda r: (r.id, r.name), "room")
        await home.move(station, room)
        log.info(f"{station.name}: moved to {room.name}")
    if args.name:
        await home.rename(station, args.name)
        log.info(f"{station.name}: renamed to {args.name}")


async def cmd_sound(ctx: Context, args: SoundOptions) -> None:
    before = await account_snapshot(ctx, args)
    if args.random_code:
        code = secrets.token_urlsafe(12)
        log.warning("using random code: Wi-Fi only, station will NOT bind to an account")
    else:
        code = await ctx.code()
    freqs = tones(config_payload(args, code))
    path = args.wav or Path(tempfile.gettempdir()) / "yandex_station_pairing.wav"
    log.info(f"{len(freqs)} symbols, {write_wav(path, freqs, args.volume):.2f}s -> {path}")
    if args.no_play:
        return
    log.info("Put the station in setup mode, keep the speaker close and the volume high.")
    for i in range(1, args.repeat + 1):
        log.info(f"playing {i}/{args.repeat} ...")
        await asyncio.to_thread(play_wav, path)
    await place_station(ctx, args, before)


async def cmd_ble_scan(ctx: Context, args: BleScanOptions) -> None:
    from ..setup.ble import ble_scan

    if not (found := await ble_scan(args.scan_timeout)):
        print("nothing found (station must be in setup mode)")
    for p in found:
        print(f"{p.address}  rssi={p.rssi}  {p.platform:22} did={p.did}  name={p.name}  adv={p.raw.hex()}")


async def cmd_ble(ctx: Context, args: BleOptions) -> None:
    from ..setup.ble import ble_setup

    before = await account_snapshot(ctx, args)
    device_id = await ble_setup(args, await ctx.oauth(), ctx.code)
    await place_station(ctx, args, before, device_id)
