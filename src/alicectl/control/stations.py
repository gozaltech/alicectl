import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..api.iot import IotDevice
from ..api.quasar import Device
from ..context import Context
from ..errors import AlicectlError
from ..lookup import pick_many
from .actions import Act
from .state import State

if TYPE_CHECKING:
    from ..api.glagol import Locator

log = logging.getLogger(__name__)

CLOUD_STEP_PAUSE = 2

type Show = Callable[[Device, State], Awaitable[str | None]]


async def ok(_: Device, __: State) -> str:
    return "ok"


class Stations:
    def __init__(self, ctx: Context, query: str) -> None:
        self.ctx = ctx
        self.query = query
        self.cloud_devices: dict[str, IotDevice] | None = None
        self.locator: Locator | None = None
        self.cloud_lock = asyncio.Lock()
        self.unreachable: set[str] = set()

    def pick(self, devices: list[Device]) -> list[Device]:
        stations = [d for d in devices if not d.is_app]
        return pick_many(stations, self.query, lambda d: (d.id, d.name), "station")

    async def targets(self) -> list[Device]:
        try:
            return self.pick(await self.ctx.devices())
        except AlicectlError:
            if self.ctx.devices_fresh:
                raise
        return self.pick(await self.ctx.devices(refresh=True))

    async def run(self, device: Device, act: Act) -> State:
        from ..api.glagol import Locator, Unreachable, open_station

        ctx = self.ctx
        if device.id not in self.unreachable:
            self.locator = self.locator or Locator(lambda: ctx.devices(refresh=True))
            offline = ctx.offline(device)
            try:
                token = await ctx.glagol_token(device)
                async with open_station(device, token, self.locator, search=not offline) as station:
                    if station.device != device:
                        ctx.remember(station.device)
                    if offline:
                        ctx.set_offline(device, False)
                    return State.local(await act.perform(station))
            except Unreachable as e:
                log.debug(f"{device.name}: {e}; using the cloud")
                self.unreachable.add(device.id)
                if not offline:
                    ctx.set_offline(device, True)
            except AlicectlError:
                ctx.forget_token(device)
                raise
        return await self.run_cloud(device, act)

    async def run_cloud(self, device: Device, act: Act) -> State:
        if act.local_only:
            raise AlicectlError(f"not reachable on the local network, and {act.local_only} works only there")
        home = await self.ctx.home()
        async with self.cloud_lock:
            if self.cloud_devices is None:
                self.cloud_devices = {d.quasar_id: d for d in (await home.devices()) if d.quasar_id}
        if not (remote := self.cloud_devices.get(device.id)):
            raise AlicectlError("not reachable locally and not in the smart-home list")
        if cloud := act.cloud:
            for step in range(act.repeat):
                if step:
                    await asyncio.sleep(CLOUD_STEP_PAUSE)
                await home.act(remote, cloud)
        return State.remote(remote)

    async def each(self, act: Act, show: Show = ok) -> None:
        targets = await self.targets()

        async def one(device: Device) -> tuple[bool, str | None]:
            try:
                state = await self.run(device, act)
                line = await show(device, state)
                return True, f"{line} (via cloud)" if line and state.cloud else line
            except AlicectlError as e:
                return False, str(e)

        results = await asyncio.gather(*(one(device) for device in targets))
        if len(targets) == 1:
            match results[0]:
                case False, error:
                    raise AlicectlError(f"{targets[0].name}: {error}")
                case True, str(line):
                    print(line)
            return
        for device, (success, text) in zip(targets, results, strict=True):
            if not success:
                log.error(f"{device.name}: {text}")
            elif text:
                print(f"{device.name}: {text}")
        if not all(success for success, _ in results):
            raise AlicectlError()
