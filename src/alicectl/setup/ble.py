import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING

import httpx2
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from ..api.quasar import encrypt
from ..errors import AlicectlError
from .payload import config_payload

if TYPE_CHECKING:
    from ..options import BleOptions

log = logging.getLogger(__name__)

SERVICE = "664578b6-c390-42d8-956e-c1ae8b646436"
CHAR_DEVICE_ID = "4276d829-46ce-4ce9-9f30-30dbb9a43724"
CHAR_CREDENTIALS = "f25d4a0d-6266-4254-943e-3e20606f2fa6"
CHAR_STATUS = "9879f69f-510f-4a0c-b500-79de90342997"
COMPANY_ID = 2309
SETUP_FLAG_MODELS = {23, 41}
WRITE_CHUNK = 512
CODE_MAX_AGE = 480
MODELS = {
    1: "yandexstation",
    2: "yandexstation_2",
    3: "yandexmini",
    4: "yandexmini_2",
    5: "yandexmicro",
    6: "yandexmini_2_no_clock",
    7: "yandexmidi",
    8: "centaur",
    9: "chiron",
    10: "saturn",
    11: "yandexmodule_2",
    12: "goya",
    13: "magritte",
    14: "yandex_tv_mt9632_cv",
    15: "quinglong",
    16: "cucumber",
    17: "plum",
    18: "blackthorn",
    19: "pickle",
    20: "mike",
    21: "bergamot",
    22: "mango",
    23: "cherry",
    24: "monet",
    26: "magritte_2",
    27: "malevich",
    28: "starling",
    29: "prune",
    30: "orion",
    31: "iot_cloud_device",
    32: "nebula",
    33: "jay",
    34: "repa",
    35: "brukva",
    36: "nota",
    37: "monocle",
    38: "muffin",
    39: "boo",
    41: "blueberry",
    42: "avis",
}
STATUSES = {
    0: "NONE",
    1: "SOMETHING_WENT_WRONG",
    2: "CANNOT_CONNECT_TO_THE_WIFI",
    3: "WRONG_WIFI_PASSWORD",
    4: "NO_ACCESS_WIFI",
    5: "NO_ACCESS_ETHERNET",
    6: "PROTOCOL_NOT_SUPPORTED",
    7: "GENERIC_BLE_ERROR",
    100: "STARTED",
    101: "NETWORK_CONNECT_OK",
    102: "BACKEND_REGISTER_OK",
    103: "UPDATE_DOWNLOAD_STARTED",
    104: "UPDATE_NOT_REQUIRED",
    105: "UPDATE_DOWNLOAD_FINISHED",
    106: "UPDATE_DOWNLOAD_INTERRUPTED",
    107: "UPDATE_IS_REQUIRED",
    108: "CONFIGURATION_FINISHED",
    109: "OK",
}
STATUS_OK = 109


@dataclass(slots=True)
class Peripheral:
    address: str
    name: str | None = None
    rssi: int | None = None
    platform: str = "unknown"
    did: str | None = None
    raw: bytes = b""
    device: BLEDevice | None = None


async def ble_scan(seconds: float) -> list[Peripheral]:
    found = []
    for dev, adv in (await BleakScanner.discover(timeout=seconds, return_adv=True)).values():
        match bytes(adv.manufacturer_data.get(COMPANY_ID, b"")):
            case b"":
                continue
            case raw if raw[0] in SETUP_FLAG_MODELS and raw[2:3] == b"\x00":
                continue
            case raw:
                did = raw[9:22].decode("ascii", "replace") if len(raw) >= 22 else None
                found.append(
                    Peripheral(
                        dev.address,
                        adv.local_name or dev.name,
                        adv.rssi,
                        MODELS.get(raw[0], f"unknown({raw[0]})"),
                        did,
                        raw,
                        dev,
                    )
                )
    return sorted(found, key=lambda p: -(p.rssi or -999))


async def find_station(address: str | None, limit: float, scan_seconds: float) -> Peripheral:
    up_to = f" for up to {limit:g}s" if limit else ""
    log.info(f"waiting for {address or 'a station'} in setup mode{up_to} (Ctrl+C to stop) ...")
    try:
        async with asyncio.timeout(limit or None):
            while True:
                for p in await ble_scan(scan_seconds):
                    if address is None or p.address.casefold() == address.casefold():
                        log.info(f"found {p.address} {p.name or ''} ({p.platform}, rssi {p.rssi})")
                        return p
    except TimeoutError:
        raise AlicectlError(f"no station in setup mode appeared within {limit:g}s") from None


def unframe(data: bytes) -> tuple[int, bytes]:
    match data[:1]:
        case b"\x01":
            return int.from_bytes(data[1:5]), data[5:]
        case _:
            return len(data) - 1, data[1:]


class BleSetup:
    def __init__(
        self, args: BleOptions, oauth: str, new_code: Callable[[], Awaitable[str]], target: Peripheral
    ) -> None:
        self.args, self.oauth, self.new_code, self.target = args, oauth, new_code, target
        self.platform = args.platform or target.platform
        if self.platform.startswith("unknown"):
            raise AlicectlError("unknown model, pass --platform (e.g. yandexstation_2)")
        self.loop = asyncio.get_running_loop()
        self.done = asyncio.Event()
        self.device_id: str | None = None
        self.plaintext = b""
        self.issued = -math.inf
        self.message: str | None = None
        self.written = False
        self.status: str | None = None
        self.deadline: asyncio.Timeout | None = None

    async def prepare(self) -> None:
        if time.monotonic() - self.issued > CODE_MAX_AGE:
            self.plaintext = config_payload(self.args, await self.new_code())
            self.issued, self.message = time.monotonic(), None
            log.debug(f"plaintext: {self.plaintext.hex()}")
        if self.device_id and not self.message:
            self.message = await encrypt(self.plaintext, self.device_id, self.platform, self.oauth)

    def on_status(self, _, data: bytearray) -> None:
        try:
            status = json.loads(unframe(bytes(data))[1])["status"]
        except ValueError, KeyError, TypeError:
            log.info(f"status (raw): {bytes(data)!r}")
            return
        name = STATUSES.get(status, f"UNKNOWN({status})")
        log.info(f"station: {name}")
        if self.deadline and not self.deadline.expired():
            self.deadline.reschedule(self.loop.time() + self.args.wait)
        if status < 100 or status == STATUS_OK:
            self.status = name
            self.done.set()

    async def read_device_id(self, client: BleakClient) -> str:
        total, buf = unframe(bytes(await client.read_gatt_char(CHAR_DEVICE_ID)))
        while len(buf) < total:
            buf += bytes(await client.read_gatt_char(CHAR_DEVICE_ID))
        log.info(f"device id: {buf[:total].decode()}")
        return buf[:total].decode()

    async def write_credentials(self, client: BleakClient, device_id: str) -> None:
        if not self.message:
            self.message = await encrypt(self.plaintext, device_id, self.platform, self.oauth)
        message = self.message.encode()
        framed = b"\x01" + len(message).to_bytes(4) + message
        step = min(WRITE_CHUNK, max(20, client.mtu_size - 3))
        log.debug(f"mtu {client.mtu_size}, writing {len(framed)} bytes in {step}-byte chunks")
        for chunk in batched(framed, step, strict=False):
            await client.write_gatt_char(CHAR_CREDENTIALS, bytes(chunk), response=True)
        self.written = True

    async def wait_for_station(self) -> None:
        wait = self.args.wait
        log.info(f"credentials written, waiting for station (up to {wait:g}s between updates) ...")
        try:
            async with asyncio.timeout(wait) as self.deadline:
                await self.done.wait()
        except TimeoutError:
            log.warning(f"no status from station for {wait:g}s (it may still finish on its own)")
        finally:
            self.deadline = None

    async def session(self) -> None:
        async with BleakClient(
            self.target.device or self.target.address,
            timeout=30,
            winrt={"use_cached_services": False},
            disconnected_callback=lambda _: log.info("BLE: station closed the connection"),
        ) as client:
            await asyncio.sleep(0.5)
            if client.services.get_service(SERVICE) is None:
                raise AlicectlError("setup service not found - is the station in setup mode?")
            self.device_id = self.device_id or await self.read_device_id(client)
            await client.start_notify(CHAR_STATUS, self.on_status)
            await self.write_credentials(client, self.device_id)
            await self.wait_for_station()

    async def run(self) -> str | None:
        args = self.args
        for attempt in range(1, args.retries + 1):
            await self.prepare()
            try:
                await self.session()
                break
            except (BleakError, OSError, TimeoutError, httpx2.TransportError) as e:
                if self.written:
                    if not self.done.is_set():
                        log.warning(
                            f"BLE closed after credentials were written ({e}); check the station for the result"
                        )
                    break
                log.warning(f"BLE attempt {attempt}/{args.retries} failed: {e!r}")
                if attempt == args.retries:
                    raise AlicectlError("BLE setup failed; put the station back into setup mode and retry") from None
                await asyncio.sleep(2)
                self.target = await find_station(self.target.address, args.find_timeout, args.scan_timeout)
        if self.status not in {None, "OK"}:
            raise AlicectlError(f"setup failed: {self.status}")
        return self.device_id


async def ble_setup(args: BleOptions, oauth: str, new_code: Callable[[], Awaitable[str]]) -> str | None:
    target = await find_station(args.address, args.find_timeout, args.scan_timeout)
    return await BleSetup(args, oauth, new_code, target).run()
