import asyncio
import logging

from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

log = logging.getLogger(__name__)

SERVICE = "_yandexio._tcp.local."
BROWSE_SECONDS = 3
RESOLVE_MS = 3000


async def discover(found: dict[str, tuple[str, int]], changed: asyncio.Event, seconds: float = BROWSE_SECONDS) -> None:
    resolving: set[asyncio.Task[None]] = set()

    async def resolve(zeroconf: Zeroconf, name: str) -> None:
        info = AsyncServiceInfo(SERVICE, name)
        if not await info.async_request(zeroconf, RESOLVE_MS):
            return
        props = {key.decode(): value.decode() for key, value in info.properties.items() if value}
        if (device_id := props.get("deviceId")) and (ips := info.parsed_addresses()) and info.port:
            found[device_id] = (next((ip for ip in ips if "." in ip), ips[0]), info.port)
            changed.set()

    def on_change(zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Added:
            task = asyncio.ensure_future(resolve(zeroconf, name))
            resolving.add(task)
            task.add_done_callback(resolving.discard)

    try:
        async with AsyncZeroconf() as zc:
            browser = AsyncServiceBrowser(zc.zeroconf, [SERVICE], handlers=[on_change])
            try:
                await asyncio.sleep(seconds)
            finally:
                await browser.async_cancel()
                for task in list(resolving):
                    task.cancel()
    except OSError as e:
        log.debug(f"mDNS discovery failed: {e!r}")
    log.debug(f"mDNS found {len(found)} station(s)")
