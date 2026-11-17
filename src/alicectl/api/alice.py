from dataclasses import dataclass, field
from typing import Any

from .iot import IotDevice
from .web import WebSession

ALARM_EXPERIMENTS = ("rpc_alarm_default_sounds_on",)
SOUND_TRACK, SOUND_MELODY = "Track", "DefaultSound"


def api_days(days: tuple[str, ...]) -> list[str]:
    return [day.capitalize() for day in days]


def our_days(days: list[str] | None) -> tuple[str, ...]:
    return tuple(day.lower() for day in days or ())


def track_sound(track_id: str) -> dict:
    return {"content_id": track_id, "type": SOUND_TRACK}


def melody_sound(name: str) -> dict:
    return {"content_id": name, "type": SOUND_MELODY}


@dataclass(slots=True, frozen=True)
class Alarm:
    id: str
    device_id: str
    time: str
    enabled: bool
    name: str = ""
    date: str = ""
    days: tuple[str, ...] = ()
    sound: dict | None = None
    raw: dict = field(default_factory=dict, compare=False)

    @classmethod
    def parse(cls, data: dict) -> Alarm:
        return cls(
            id=data.get("alarm_id", ""),
            device_id=data.get("device_id", ""),
            time=data.get("time", ""),
            enabled=bool(data.get("enabled")),
            name=data.get("name") or "",
            date=data.get("date") or "",
            days=our_days((data.get("recurring") or {}).get("days_of_week")),
            sound=data.get("media_settings"),
            raw=data,
        )


@dataclass(slots=True, frozen=True)
class Reminder:
    id: str
    text: str
    at: str = ""
    days: tuple[str, ...] = ()
    time: str = ""
    device_ids: tuple[str, ...] = ()

    @classmethod
    def parse(cls, data: dict) -> Reminder:
        trigger = data.get("trigger_policy") or {}
        single = trigger.get("single_trigger_policy") or {}
        weekly = trigger.get("weekdays_trigger_policy") or {}
        location = ((data.get("play_settings") or {}).get("play_location") or {}).get("selected_devices") or {}
        return cls(
            id=data.get("id", ""),
            text=data.get("text", ""),
            at=str(single.get("datetime", "")),
            days=our_days(weekly.get("day_of_week")),
            time=str(weekly.get("time", "")),
            device_ids=tuple(location.get("selected_device_ids") or []),
        )


@dataclass(slots=True, frozen=True)
class AlarmDefaults:
    volume: int
    gradual: bool
    sound: dict | None
    melodies: tuple[str, ...]

    @classmethod
    def parse(cls, data: dict) -> AlarmDefaults:
        sound = data.get("sound_settings") or {}
        return cls(
            volume=int(sound.get("volume") or 0),
            gradual=bool(sound.get("gradual_volume_increase")),
            sound=data.get("media_settings"),
            melodies=tuple(data.get("supported_default_sounds") or ()),
        )

    def body(self) -> dict:
        body: dict = {"sound_settings": {"volume": self.volume, "gradual_volume_increase": self.gradual}}
        if self.sound:
            body["media_settings"] = self.sound
        return body


async def alarm_rpc(web: WebSession, path: str, body: dict) -> dict:
    return await web.rpc("POST", path, body, experiments=ALARM_EXPERIMENTS)


async def get_alarms(web: WebSession, device_ids: list[str]) -> tuple[list[Alarm], list[str]]:
    data = await alarm_rpc(web, "/get_alarms", {"device_ids": device_ids})
    return [Alarm.parse(a) for a in data.get("alarms") or []], data.get("failed_device_ids") or []


async def create_alarm(
    web: WebSession,
    device: IotDevice,
    clock: str,
    on: str | None,
    days: tuple[str, ...],
    name: str = "",
    sound: dict | None = None,
) -> None:
    alarm: dict = {"device_id": device.quasar_id, "enabled": True, "time": clock}
    if name:
        alarm["name"] = name
    if sound:
        alarm["media_settings"] = sound
    if days:
        alarm["recurring"] = {"days_of_week": api_days(days)}
    else:
        alarm["date"] = on
    await alarm_rpc(web, "/create_alarm", {"alarm": alarm, "device_type": device.type})


async def change_alarm(web: WebSession, alarm: Alarm, device: IotDevice, **changes: Any) -> None:
    await alarm_rpc(web, "/change_alarm", {"alarm": {**alarm.raw, **changes}, "device_type": device.type})


async def cancel_alarms(web: WebSession, alarms: list[Alarm]) -> None:
    ids = [{"alarm_id": alarm.id, "device_id": alarm.device_id} for alarm in alarms]
    await alarm_rpc(web, "/cancel_alarms", {"device_alarm_ids": ids})


async def get_alarm_defaults(web: WebSession, device: IotDevice) -> AlarmDefaults:
    data = await alarm_rpc(web, "/get_device_alarm_settings", {"device_id": device.quasar_id})
    return AlarmDefaults.parse(data.get("device_alarm_settings") or {})


async def set_alarm_defaults(web: WebSession, device: IotDevice, defaults: AlarmDefaults) -> None:
    body = {"device_id": device.quasar_id, "device_alarm_settings": defaults.body()}
    await alarm_rpc(web, "/set_device_alarm_settings", body)


def is_archived(reminder: dict) -> bool:
    archived = ((reminder.get("properties") or {}).get("archived_property") or {}).get("manual_archived_datetime")
    return bool(archived)


async def get_reminders(web: WebSession) -> list[Reminder]:
    data = await web.rpc("GET", "/get_reminders")
    return [Reminder.parse(r) for r in data.get("reminders") or [] if not is_archived(r)]


async def create_reminder(
    web: WebSession,
    text: str,
    at: str | None,
    days: tuple[str, ...],
    clock: str,
    device_ids: list[str],
    push: bool,
) -> None:
    if at is not None:
        trigger = {"single_trigger_policy": {"datetime": at}}
    else:
        trigger = {"weekdays_trigger_policy": {"day_of_week": api_days(days), "time": clock}}
    play: dict = {"send_push": {"state": "Enabled" if push else "Disabled"}}
    if device_ids:
        play["play_location"] = {"selected_devices": {"selected_device_ids": device_ids}}
    await web.rpc("POST", "/create_reminder", {"text": text, "trigger_policy": trigger, "play_settings": play})


async def cancel_reminders(web: WebSession, reminders: list[Reminder]) -> None:
    await web.rpc("POST", "/cancel_reminders", {"cancel_selected": {"reminder_ids": [r.id for r in reminders]}})
