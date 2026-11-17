from dataclasses import dataclass

from ..errors import AlicectlError
from ..lookup import pick
from .http import json_dict, request

MUSIC_URL = "https://api.music.yandex.net"
KINDS = ["track", "album", "artist", "playlist"]
LIKES_PLAYLIST_KIND = 3
WAVE = "user:onyourwave"


@dataclass(slots=True, frozen=True)
class MusicItem:
    kind: str
    id: str
    title: str
    artist: str = ""

    def __str__(self) -> str:
        return f"{self.kind} «{self.title}»" + (f" by {self.artist}" if self.artist else "")


def artists_of(data: dict) -> str:
    return ", ".join(a.get("name", "") for a in data.get("artists") or [])


def parse_item(kind: str, data: dict) -> MusicItem:
    match kind:
        case "track" | "album":
            return MusicItem(kind, str(data["id"]), data.get("title", ""), artists_of(data))
        case "artist":
            return MusicItem(kind, str(data["id"]), data.get("name", ""))
        case "playlist":
            owner = (data.get("owner") or {}).get("uid") or data.get("uid")
            return MusicItem(
                kind, f"{owner}:{data['kind']}", data.get("title", ""), (data.get("owner") or {}).get("name", "")
            )
    raise AlicectlError(f"can't play a {kind} yet")


class Music:
    def __init__(self, oauth: str) -> None:
        self.headers = {"Authorization": f"OAuth {oauth}"}
        self._uid: str | None = None

    async def get(self, path: str, **params) -> dict | list:
        resp = await request("GET", MUSIC_URL + path, headers=self.headers, params=params or None)
        if resp.status_code != 200 or "result" not in (data := json_dict(resp)):
            raise AlicectlError(f"Yandex Music {path} failed (HTTP {resp.status_code}): {resp.text[:300]}")
        return data["result"]

    async def post(self, path: str, form: dict) -> None:
        resp = await request("POST", MUSIC_URL + path, headers=self.headers, data=form)
        if resp.status_code != 200:
            raise AlicectlError(f"Yandex Music {path} failed (HTTP {resp.status_code}): {resp.text[:300]}")

    async def uid(self) -> str:
        if self._uid is None:
            status = await self.get("/account/status")
            assert isinstance(status, dict)
            self._uid = str(status["account"]["uid"])
        return self._uid

    async def search(self, text: str, kind: str | None = None) -> MusicItem:
        found = await self.get("/search", text=text, type="all", page=0, nocorrect="False")
        assert isinstance(found, dict)
        if kind is None and (best := found.get("best")) and best.get("type") in KINDS:
            return parse_item(best["type"], best["result"])
        for candidate in [kind] if kind else KINDS:
            if results := (found.get(f"{candidate}s") or {}).get("results"):
                return parse_item(candidate, results[0])
        raise AlicectlError(f"nothing found on Yandex Music for {text!r}")

    async def likes(self) -> MusicItem:
        return MusicItem("playlist", f"{await self.uid()}:{LIKES_PLAYLIST_KIND}", "Мне нравится")

    async def playlist(self, name: str) -> MusicItem:
        uid = await self.uid()
        playlists = await self.get(f"/users/{uid}/playlists/list")
        assert isinstance(playlists, list)
        chosen = pick(playlists, name, lambda p: (str(p.get("kind", "")), p.get("title", "")), "playlist")
        return parse_item("playlist", {**chosen, "uid": uid})

    async def marked(self, opinion: str) -> set[str]:
        result = await self.get(f"/users/{await self.uid()}/{opinion}s/tracks")
        assert isinstance(result, dict)
        return {str(track["id"]) for track in (result.get("library") or {}).get("tracks") or []}

    async def mark(self, track_id: str, opinion: str, undo: bool = False) -> bool:
        if (track_id in await self.marked(opinion)) != undo:
            return False
        action = "remove" if undo else "add-multiple"
        await self.post(f"/users/{await self.uid()}/{opinion}s/tracks/{action}", {"track-ids": track_id})
        return True
