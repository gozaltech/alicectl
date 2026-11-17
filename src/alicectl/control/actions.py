import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ..api.music import LIKES_PLAYLIST_KIND, WAVE, MusicItem
from ..errors import AlicectlError
from .state import State

if TYPE_CHECKING:
    from ..api.glagol import Station

log = logging.getLogger(__name__)

SERVER_ACTION = "devices.capabilities.quasar.server_action"
REPEAT_MODES = {"one": "One", "all": "All"}
PLAYER = {
    "play": ("play", "продолжи"),
    "pause": ("stop", "пауза"),
    "next": ("next", "следующий трек"),
    "prev": ("prev", "предыдущий трек"),
}
SEEKS = ["forward", "rewind", "seek"]
TRACK_STEPS = {"next", "prev"}
SEEK_STEP = timedelta(seconds=10)
TRACK_SWITCH_TIMEOUT = 6
POLL_INTERVAL = 0.25

type Script = Callable[[Station], Awaitable[dict]]


@dataclass(slots=True, frozen=True)
class Act:
    local: dict | Script
    command: str = ""
    speak: bool = False
    repeat: int = 1
    local_only: str = ""

    @property
    def cloud(self) -> tuple[str, str, Any] | None:
        return (SERVER_ACTION, "phrase_action" if self.speak else "text_action", self.command) if self.command else None

    async def perform(self, station: Station) -> dict:
        return await (self.local(station) if callable(self.local) else station.send(self.local))


PING = {"command": "ping"}
STATUS = Act(PING)


def say(text: str) -> Act:
    form = {
        "name": "personal_assistant.scenarios.quasar.iot.repeat_phrase",
        "slots": [{"type": "string", "name": "phrase_to_repeat", "value": text}],
    }
    payload = {"type": "server_action", "name": "update_form", "payload": {"form_update": form, "resubmit": True}}
    return Act({"command": "serverAction", "serverActionEventPayload": payload}, text, speak=True)


def ask(text: str, quiet: bool = False) -> Act:
    payload = {"command": "sendText", "text": text}
    return Act(muted(payload), text, local_only="--quiet") if quiet else Act(payload, text)


def set_volume(level: int) -> dict:
    return {"command": "setVolume", "volume": level / 10}


def volume(level: int) -> Act:
    return Act(set_volume(level), f"громкость {level}")


def player(action: str, times: int = 1) -> Act:
    command, phrase = PLAYER[action]
    payload = {"command": command}
    return Act(step_tracks(payload, times) if action in TRACK_STEPS else payload, phrase, repeat=times)


def step_tracks(payload: dict, times: int) -> Script:
    following = "hasNext" if payload["command"] == "next" else "hasPrev"

    async def run(station: Station) -> dict:
        reply = await station.send(PING)
        for step in range(times):
            if not (reply.get("state", {}).get("playerState") or {}).get(following):
                raise AlicectlError(f"stopped after {step} of {times}: no more tracks that way")
            before = State.local(reply)
            await station.send(payload)
            reply = await track_switched(station, before)
        return reply

    return run


async def track_switched(station: Station, before: State) -> dict:
    try:
        async with asyncio.timeout(TRACK_SWITCH_TIMEOUT):
            while True:
                await asyncio.sleep(POLL_INTERVAL)
                reply = await station.send(PING)
                now = State.local(reply)
                if now.track_id != before.track_id or (now.progress or 0) + 1 < (before.progress or 0):
                    return reply
    except TimeoutError:
        raise AlicectlError(f"the track didn't change within {TRACK_SWITCH_TIMEOUT}s") from None


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def spoken(span: timedelta) -> str:
    hours, rest = divmod(int(span.total_seconds()), 3600)
    minutes, seconds = divmod(rest, 60)
    parts = [
        plural(hours, "час", "часа", "часов") if hours else "",
        plural(minutes, "минуту", "минуты", "минут") if minutes else "",
        plural(seconds, "секунду", "секунды", "секунд") if seconds else "",
    ]
    return " ".join(part for part in parts if part) or "0 секунд"


def timer(span: timedelta | None, quiet: bool = False) -> Act:
    return ask(f"поставь таймер на {spoken(span)}" if span else "отмени все таймеры", quiet)


def seek_to(seconds: float) -> dict:
    return {"command": "rewind", "position": seconds}


def seek(position: timedelta) -> Act:
    return Act(seek_to(position.total_seconds()), local_only="seek")


def skip(span: timedelta, forward: bool) -> Act:
    offset = span.total_seconds() if forward else -span.total_seconds()

    async def run(station: Station) -> dict:
        state = State.local(await station.send(PING))
        if state.progress is None or not state.duration:
            raise AlicectlError("nothing to seek in: no track with a known length is playing")
        return await station.send(seek_to(max(0, min(state.progress + offset, state.duration - 1))))

    return Act(run, f"перемотай {'вперёд' if forward else 'назад'} на {spoken(span)}")


def play(item: MusicItem, shuffle: bool = False, repeat: str | None = None) -> Act:
    local: dict = {"command": "playMusic", "type": item.kind, "id": item.id}
    if shuffle:
        local["shuffle"] = True
    if repeat:
        local["repeat"] = REPEAT_MODES[repeat]
    match item:
        case MusicItem(kind="radio", id=item_id) if item_id == WAVE:
            phrase = "включи мою волну"
        case MusicItem(kind="playlist", id=item_id) if item_id.endswith(f":{LIKES_PLAYLIST_KIND}"):
            phrase = "включи мои лайки"
        case MusicItem(kind="playlist"):
            phrase = f"включи плейлист {item.title}"
        case _:
            phrase = f"включи {item.title} {item.artist}".strip()
    return Act(local, phrase)


def muted(payload: dict) -> Script:
    async def run(station: Station) -> dict:
        if not (level := State.local(await station.send(PING)).volume):
            return await station.send(payload)
        await station.send(set_volume(0))
        try:
            reply = await station.send(payload)
            if not await station.wait_until_idle():
                log.warning("Alice didn't go idle in time; turning the volume back up anyway")
        finally:
            try:
                await station.send(set_volume(level))
            except AlicectlError as e:
                raise AlicectlError(f"couldn't restore the volume to {level}, it's still 0: {e}") from None
        return reply

    return run
