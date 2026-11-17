import asyncio
import logging
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self

from .api.auth import DEVICE_NAME, oauth_token, x_token_by_device, x_token_code
from .api.http import http_client
from .api.iot import SmartHome
from .api.music import Music
from .api.quasar import Device, device_list, glagol_token, token_expiry
from .api.web import WebSession
from .config import TOKEN_FILE, Cache, account_id, load_token, save_token

if TYPE_CHECKING:
    from .options import Options

log = logging.getLogger(__name__)

TOKEN_REFRESH_MARGIN = timedelta(minutes=10)
OFFLINE_MEMORY = timedelta(minutes=15)


class Context:
    def __init__(self, args: Options) -> None:
        self.args = args
        self._x_token: str | None = getattr(args, "x_token", None)
        self._oauth: str | None = getattr(args, "oauth_token", None)
        self._web: WebSession | None = None
        self._cache: Cache | None = None
        self._devices: list[Device] | None = None
        self.devices_fresh = False
        self._token_lock = asyncio.Lock()
        self._oauth_lock = asyncio.Lock()
        self._devices_lock = asyncio.Lock()
        self._web_lock = asyncio.Lock()
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> Self:
        await self._stack.__aenter__()
        await self._stack.enter_async_context(http_client())
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    def use(self, args: Options) -> None:
        self.args = args
        self.devices_fresh = False

    async def x_token(self) -> str:
        async with self._token_lock:
            if self._x_token is None:
                token = load_token()
                if token is None or token.expired:
                    reason = "master token expired" if token else f"no master token in {TOKEN_FILE}"
                    log.info(f"{reason}: logging in with device flow")
                    token = await x_token_by_device(DEVICE_NAME)
                    save_token(token)
                self._x_token = token.x_token
            return self._x_token

    @property
    def cache(self) -> Cache:
        if self._cache is None:
            saved = load_token()
            self._cache = Cache(account_id(self._x_token or (saved and saved.x_token) or self._oauth or ""))
            self._stack.callback(self._cache.save)
        return self._cache

    async def oauth(self) -> str:
        async with self._oauth_lock:
            if (token := self._oauth) is None:
                if not (token := self.cache.get("oauth")):
                    token, expires_at = await oauth_token(await self.x_token())
                    self.cache.put("oauth", token, expires_at and expires_at - TOKEN_REFRESH_MARGIN)
                self._oauth = token
            return token

    async def devices(self, refresh: bool = False) -> list[Device]:
        async with self._devices_lock:
            if self._devices is None and not refresh and (cached := self.cache.get("devices")):
                self._devices = [Device.load(item) for item in cached]
            elif self._devices is None or (refresh and not self.devices_fresh):
                self._devices, self.devices_fresh = await device_list(await self.oauth()), True
                self.cache.put("devices", [device.dump() for device in self._devices])
            return self._devices

    def known_devices(self) -> list[Device]:
        if self._devices is not None:
            return self._devices
        return [Device.load(item) for item in self.cache.get("devices") or []]

    def remember(self, device: Device) -> None:
        if self._devices is not None:
            self._devices = [device if d.id == device.id else d for d in self._devices]
            self.cache.put("devices", [d.dump() for d in self._devices])

    async def glagol_token(self, device: Device) -> str:
        key = f"glagol:{device.id}"
        if not (token := self.cache.get(key)):
            token = await glagol_token(await self.oauth(), device)
            expires_at = token_expiry(token)
            self.cache.put(key, token, expires_at and expires_at - TOKEN_REFRESH_MARGIN)
        return token

    def forget_token(self, device: Device) -> None:
        self.cache.drop(f"glagol:{device.id}")

    def offline(self, device: Device) -> bool:
        return bool(self.cache.get(f"offline:{device.id}"))

    def set_offline(self, device: Device, offline: bool) -> None:
        if offline:
            self.cache.put(f"offline:{device.id}", True, datetime.now(UTC) + OFFLINE_MEMORY)
        else:
            self.cache.drop(f"offline:{device.id}")

    async def code(self) -> str:
        return getattr(self.args, "code", None) or await x_token_code(await self.x_token())

    async def web(self) -> WebSession:
        async with self._web_lock:
            if self._web is None:
                self._web = await self._stack.enter_async_context(WebSession(await self.x_token()))
            return self._web

    async def home(self) -> SmartHome:
        return SmartHome(await self.web())

    async def music(self) -> Music:
        return Music(await self.oauth())
