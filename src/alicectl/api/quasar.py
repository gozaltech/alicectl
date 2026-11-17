import base64
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Self

from ..errors import AlicectlError
from .http import Form, json_dict, post, request

ENCRYPT_URL = "https://quasar.yandex.net/ui/v1.0/encrypt"
DEVICE_LIST_URL = "https://quasar.yandex.net/glagol/device_list"
GLAGOL_TOKEN_URL = "https://quasar.yandex.net/glagol/token"
ENCRYPT_TIMEOUT = 10
GLAGOL_PORT = 1961
LINK_LOCAL = "169.254."


def oauth_header(oauth: str) -> Form:
    return {"Authorization": f"OAuth {oauth}"}


async def encrypt(plaintext: bytes, device_id: str, platform: str, oauth: str) -> str:
    headers = {
        **oauth_header(oauth),
        "x-quasar-encrypt-mode": "2" if platform in {"yandexstation", "yandexmini"} else "1",
        "Content-Type": "text/plain",
    }
    resp = await post(
        ENCRYPT_URL,
        content=plaintext,
        headers=headers,
        params={"device_id": device_id, "platform": platform},
        timeout=ENCRYPT_TIMEOUT,
    )
    match resp.status_code:
        case 200 if resp.content:
            data = resp.json()
        case 204:
            raise AlicectlError("/encrypt returned 204: device does not accept encrypted config")
        case status:
            raise AlicectlError(f"/encrypt failed: HTTP {status} {resp.text[:300]}")
    keys = ("ciphertext", "M", "C") if {"M", "C"} & data.keys() else ("ciphertext",)
    return json.dumps({k: data[k] for k in keys}, separators=(",", ":"))


@dataclass(slots=True, frozen=True)
class Device:
    id: str
    name: str
    platform: str
    wifi: str = ""
    ip: str = ""
    port: int = GLAGOL_PORT
    certificate: str = ""
    updated: datetime | None = None

    @property
    def is_app(self) -> bool:
        return "_app_" in self.platform

    def dump(self) -> dict:
        return asdict(self) | {"updated": self.updated.isoformat() if self.updated else None}

    @classmethod
    def load(cls, data: dict) -> Self:
        fields = dict(data)
        if updated := fields.get("updated"):
            fields["updated"] = datetime.fromisoformat(updated)
        return cls(**fields)


async def device_list(oauth: str) -> list[Device]:
    resp = await request("GET", DEVICE_LIST_URL, headers=oauth_header(oauth))
    data = json_dict(resp)
    if data.get("status") != "ok":
        raise AlicectlError(f"device list failed (HTTP {resp.status_code}): {resp.text[:300]}")
    devices = []
    for item in data.get("devices", []):
        net = item.get("networkInfo") or {}
        ips = net.get("ip_addresses") or []
        ts = net.get("ts")
        devices.append(
            Device(
                id=item.get("id", ""),
                name=item.get("name") or (item.get("config") or {}).get("name", ""),
                platform=item.get("platform", ""),
                wifi=net.get("wifi_ssid", ""),
                ip=next((ip for ip in ips if "." in ip and not ip.startswith(LINK_LOCAL)), ""),
                port=net.get("external_port") or GLAGOL_PORT,
                certificate=((item.get("glagol") or {}).get("security") or {}).get("server_certificate", ""),
                updated=datetime.fromtimestamp(ts, UTC) if ts else None,
            )
        )
    return devices


def token_expiry(jwt: str) -> datetime | None:
    try:
        claims = json.loads(base64.urlsafe_b64decode(jwt.split(".")[1] + "=="))
        return datetime.fromtimestamp(claims["exp"], UTC)
    except IndexError, ValueError, KeyError, TypeError:
        return None


async def glagol_token(oauth: str, device: Device) -> str:
    resp = await request(
        "GET",
        GLAGOL_TOKEN_URL,
        params={"device_id": device.id, "platform": device.platform},
        headers=oauth_header(oauth),
    )
    if not (token := json_dict(resp).get("token")):
        raise AlicectlError(f"no local control token for {device.name} (HTTP {resp.status_code}): {resp.text[:300]}")
    return token
