import logging
from dataclasses import dataclass
from typing import Any, Self

from .web import WebSession

log = logging.getLogger(__name__)

IOT_URL = "https://iot.quasar.yandex.ru"
QUASAR_URL = "https://quasar.yandex.ru"


@dataclass(slots=True)
class AccountConfig:
    config: dict
    localization: dict
    surface: dict


class SmartHome:
    def __init__(self, web: WebSession) -> None:
        self.web = web

    async def call(self, method: str, path: str, **kwargs: Any) -> dict:
        return await self.web.request(method, IOT_URL + path, **kwargs)

    async def devices(self) -> list[IotDevice]:
        return (await self.overview())[0]

    async def stations(self) -> list[IotDevice]:
        return [device for device in await self.devices() if device.quasar_id]

    async def overview(self) -> tuple[list[IotDevice], list[Room]]:
        data = await self.call("GET", "/m/v3/user/devices")
        devices, rooms = [], []
        for household in data.get("households", []):
            rooms += [Room(r["id"], r.get("name", ""), household["id"]) for r in household.get("rooms") or []]
            devices += [IotDevice.parse(item, household["id"]) for item in household.get("all") or []]
        return devices, rooms

    async def rename(self, device: IotDevice, name: str) -> None:
        await self.call("PUT", f"/m/user/devices/{device.id}/name", json={"old_name": device.name, "new_name": name})

    async def remove(self, device: IotDevice) -> None:
        await self.call("DELETE", f"/m/user/devices/{device.id}")

    async def move(self, device: IotDevice, room: Room) -> None:
        await self.call("PUT", f"/m/user/devices/{device.id}/room", json={"room": room.id})

    async def act(self, device: IotDevice, *actions: tuple[str, str, Any]) -> None:
        body = [{"type": kind, "state": {"instance": instance, "value": value}} for kind, instance, value in actions]
        await self.call("POST", f"/m/user/devices/{device.id}/actions", json={"actions": body})

    async def station_config(self, device: IotDevice) -> tuple[dict, str]:
        data = await self.call("GET", f"/m/v2/user/devices/{device.id}/configuration")
        return data.get("quasar_config") or {}, data.get("quasar_config_version", "")

    async def set_station_config(self, device: IotDevice, config: dict, version: str) -> None:
        body = {"config": config, "version": version}
        await self.call("POST", f"/m/v3/user/devices/{device.id}/configuration/quasar", json=body)

    async def account_config(self) -> AccountConfig:
        data = await self.web.request("GET", f"{QUASAR_URL}/ui/v2.0/get_account_config")
        return AccountConfig(
            data.get("config") or {}, data.get("localization_config") or {}, data.get("surface_config") or {}
        )

    async def set_account_config(self, account: AccountConfig) -> None:
        body = {
            "config": account.config,
            "localization_config": account.localization,
            "surface_config": account.surface,
        }
        await self.web.request("POST", f"{QUASAR_URL}/ui/v2.0/set_account_config", json=body)

    async def scenarios(self) -> list[Scenario]:
        data = await self.call("GET", "/m/user/scenarios")
        return [
            Scenario(s["id"], s.get("name", ""), bool(s.get("is_active", True)), bool(s.get("executable", True)))
            for s in data.get("scenarios", [])
            if not s.get("archived")
        ]

    async def run_scenario(self, scenario: Scenario) -> None:
        await self.call("POST", f"/m/user/scenarios/{scenario.id}/actions")


@dataclass(slots=True, frozen=True)
class Room:
    id: str
    name: str
    household_id: str


@dataclass(slots=True, frozen=True)
class Capability:
    full_type: str
    instance: str
    value: Any = None
    parameters: dict | None = None
    state: dict | None = None

    @property
    def kind(self) -> str:
        return self.full_type.rsplit(".", 1)[-1]

    @property
    def action(self) -> tuple[str, str]:
        return self.full_type, self.instance

    @classmethod
    def parse(cls, data: dict) -> Self:
        params, state = data.get("parameters") or {}, data.get("state") or {}
        instance = params.get("instance") or state.get("instance") or ("on" if data["type"].endswith("on_off") else "")
        return cls(data["type"], instance, state.get("value"), params, state)


@dataclass(slots=True, frozen=True)
class IotDevice:
    id: str
    name: str
    type: str
    household_id: str = ""
    room: str = ""
    quasar_id: str = ""
    firmware: str = ""
    capabilities: tuple[Capability, ...] = ()

    @property
    def short_type(self) -> str:
        return self.type.removeprefix("devices.types.").split(".")[0]

    def capability(self, instance: str) -> Capability | None:
        return next((c for c in self.capabilities if c.instance == instance), None)

    @classmethod
    def parse(cls, data: dict, household_id: str) -> Self:
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            type=data.get("type", ""),
            household_id=household_id,
            room=data.get("room_name") or "",
            quasar_id=(data.get("quasar_info") or {}).get("device_id", ""),
            firmware=((data.get("parameters") or {}).get("device_info") or {}).get("sw_version") or "",
            capabilities=tuple(Capability.parse(c) for c in data.get("capabilities") or [] if c.get("type")),
        )


@dataclass(slots=True, frozen=True)
class Scenario:
    id: str
    name: str
    active: bool
    executable: bool
