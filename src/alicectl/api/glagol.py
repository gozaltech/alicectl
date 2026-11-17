import asyncio
import json
import logging
import ssl
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from ..errors import AlicectlError
from .quasar import Device

log = logging.getLogger(__name__)

FIRST_CONNECT_TIMEOUT = 1.5
CONNECT_TIMEOUT = 5
REPLY_TIMEOUT = 10
IDLE = "IDLE"
SETTLE_TIME = 2
SETTLE_LIMIT = 20


def ssl_context(certificate: str) -> ssl.SSLContext:
    context = ssl.create_default_context(cadata=certificate or None)
    context.check_hostname = False
    if not certificate:
        context.verify_mode = ssl.CERT_NONE
    return context


class Unreachable(Exception):
    pass


class Locator:
    def __init__(self, refresh: Callable[[], Awaitable[list[Device]]]) -> None:
        self.refresh = refresh
        self.found: dict[str, tuple[str, int]] = {}
        self.changed = asyncio.Event()
        self.search: asyncio.Task[None] | None = None

    async def find(self, device: Device, search: bool = True) -> Device | None:
        if self.search is None and search:
            from .mdns import discover

            self.search = asyncio.ensure_future(discover(self.found, self.changed))
            self.search.add_done_callback(lambda _: self.changed.set())
        try:
            fresh = next((d for d in await self.refresh() if d.id == device.id), device)
        except AlicectlError as e:
            log.debug(f"couldn't refresh the device list: {e}")
            fresh = device
        if fresh.ip and (fresh.ip, fresh.port, fresh.certificate) != (device.ip, device.port, device.certificate):
            return fresh
        while self.search and device.id not in self.found and not self.search.done():
            self.changed.clear()
            await self.changed.wait()
        if (address := self.found.get(device.id)) and address != (device.ip, device.port):
            return replace(fresh, ip=address[0], port=address[1])
        return None


class Station:
    def __init__(self, ws: ClientConnection, token: str, device: Device) -> None:
        self.ws = ws
        self.token = token
        self.device = device

    async def send(self, payload: dict) -> dict:
        request_id = str(uuid.uuid4())
        message = {
            "conversationToken": self.token,
            "id": request_id,
            "payload": payload,
            "sentTime": time.time_ns() // 1_000_000,
        }
        try:
            await self.ws.send(json.dumps(message, ensure_ascii=False))
            async with asyncio.timeout(REPLY_TIMEOUT):
                async for raw in self.ws:
                    reply = json.loads(raw)
                    if reply.get("requestId") != request_id:
                        continue
                    log.debug(f"reply {reply.get('status')}: state={reply.get('state')}")
                    if (status := reply.get("status")) != "SUCCESS":
                        detail = f": {text}" if (text := reply.get("errorText")) else ""
                        raise AlicectlError(f"station replied {status}{detail}")
                    return reply
        except TimeoutError:
            raise AlicectlError(f"no reply within {REPLY_TIMEOUT}s") from None
        except WebSocketException as e:
            raise AlicectlError(f"connection lost: {e!r}") from None
        raise AlicectlError("station closed the connection")

    async def wait_until_idle(self) -> bool:
        busy, settled_at = False, None
        try:
            async with asyncio.timeout(SETTLE_LIMIT):
                while True:
                    try:
                        async with asyncio.timeout_at(settled_at):
                            raw = await self.ws.recv()
                    except TimeoutError:
                        return True
                    alice = (json.loads(raw).get("state") or {}).get("aliceState")
                    if alice == IDLE and busy:
                        settled_at = settled_at or asyncio.get_running_loop().time() + SETTLE_TIME
                    elif alice and alice != IDLE:
                        busy, settled_at = True, None
        except TimeoutError:
            return False
        except WebSocketException as e:
            raise AlicectlError(f"connection lost: {e!r}") from None


async def _connect(device: Device, seconds: float) -> ClientConnection:
    host = f"[{device.ip}]" if ":" in device.ip else device.ip
    url = f"wss://{host}:{device.port}"
    try:
        return await connect(url, ssl=ssl_context(device.certificate), open_timeout=seconds, proxy=None, max_size=None)
    except (OSError, TimeoutError, WebSocketException) as e:
        raise Unreachable(f"{url}: {e!r}") from None


@asynccontextmanager
async def open_station(device: Device, token: str, locator: Locator, search: bool = True) -> AsyncIterator[Station]:
    if not device.certificate:
        log.warning(f"{device.name}: no certificate on record, connecting without verification")
    try:
        if not device.ip:
            raise Unreachable("no IP address on record")
        ws = await _connect(device, FIRST_CONNECT_TIMEOUT)
    except Unreachable as first:
        log.debug(f"{device.name}: {first}; looking for it")
        if (found := await locator.find(device, search)) is None:
            raise Unreachable(f"cannot reach {first} and couldn't find it on the network") from None
        log.info(f"{device.name}: found at {found.ip} on the network")
        device = found
        ws = await _connect(device, CONNECT_TIMEOUT)
    async with ws:
        yield Station(ws, token, device)
