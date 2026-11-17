from dataclasses import dataclass
from typing import Self

from ..api.iot import IotDevice
from ..schedule import describe_position


@dataclass(slots=True)
class State:
    volume: int | None = None
    playing: bool = False
    track_id: str = ""
    title: str = ""
    artist: str = ""
    alice: str = ""
    progress: float | None = None
    duration: float | None = None
    cloud: bool = False

    @classmethod
    def local(cls, reply: dict) -> Self:
        state = reply.get("state") or {}
        player = state.get("playerState") or {}
        return cls(
            volume=round((state.get("volume") or 0) * 10),
            playing=bool(state.get("playing")),
            track_id=str(player.get("id") or ""),
            title=player.get("title") or "",
            artist=player.get("subtitle") or "",
            alice=str(state.get("aliceState") or "").lower(),
            progress=player.get("progress") if player.get("hasProgressBar") else None,
            duration=player.get("duration") if player.get("hasProgressBar") else None,
        )

    @classmethod
    def remote(cls, device: IotDevice) -> Self:
        level = (c.value or {}).get("value") if (c := device.capability("volume")) else None
        audio = (c.state or {}) if (c := device.capability("audio_player")) else {}
        return cls(
            volume=level,
            playing="play" in str(audio.get("state_type", "")).lower(),
            track_id=str(audio.get("stream_id") or ""),
            title=audio.get("title") or "",
            artist=audio.get("subtitle") or "",
            cloud=True,
        )

    @property
    def track(self) -> str:
        return " - ".join(part for part in (self.artist, self.title) if part)

    def describe(self) -> str:
        track = self.track
        parts = [f"volume {self.volume}/10"] if self.volume is not None else []
        parts.append(f"{'playing' if self.playing else 'paused'} {track}" if track else "nothing playing")
        if track and self.progress is not None and self.duration:
            parts[-1] += f" ({describe_position(self.progress)}/{describe_position(self.duration)})"
        if self.alice:
            parts.append(f"alice {self.alice}")
        return ", ".join(parts)
