import asyncio
import logging
import secrets
import time
import webbrowser
from datetime import UTC, datetime, timedelta

import httpx2

from ..config import MasterToken
from ..errors import AlicectlError
from .http import Form, json_dict, post, post_value

log = logging.getLogger(__name__)

PASSPORT = "https://mobileproxy.passport.yandex.net"
OAUTH_URL = "https://oauth.mobile.yandex.net/1/token"
DEVICE_OAUTH = "https://oauth.yandex.ru"
DEVICE_NAME = "alicectl"
AM_CLIENT: Form = {"client_id": "c0ebe342af7d48fbbbfcf2d2eedb8f9e", "client_secret": "ad0a908f0aa341a182a37ecd75bc319e"}
QUASAR_CLIENT: Form = {
    "client_id": "ee06d0aa5b0b4fbe8ae476bb33d13721",
    "client_secret": "8308d2ea7299475a84be851fe32a57d3",
}
MUSIC_CLIENT: Form = {
    "client_id": "23cabbbdc6cd418abb4b39c32c41195d",
    "client_secret": "53bc75238f0c4d08a118e51fe9203300",
}


async def x_token_by_device(device_name: str) -> MasterToken:
    form = {"client_id": AM_CLIENT["client_id"], "device_id": secrets.token_hex(5), "device_name": device_name}
    code = await post_value(f"{DEVICE_OAUTH}/device/code", form, "device_code")
    log.info(f"Open {code['verification_url']} and enter code {code['user_code']} (valid {code['expires_in']}s)")
    webbrowser.open(code["verification_url"])
    interval, deadline = code.get("interval", 5), time.monotonic() + code["expires_in"]
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            resp = await post(
                f"{DEVICE_OAUTH}/token", data={**AM_CLIENT, "grant_type": "device_code", "code": code["device_code"]}
            )
        except httpx2.TransportError as e:
            log.debug(f"device login poll failed: {e!r}")
            continue
        match json_dict(resp):
            case {"access_token": str(x_token), **rest}:
                expires_in = rest.get("expires_in")
                return MasterToken(x_token, datetime.now(UTC) + timedelta(seconds=expires_in) if expires_in else None)
            case {"error": "authorization_pending"}:
                pass
            case {"error": "slow_down"}:
                interval += 5
            case _ if resp.is_server_error:
                pass
            case error:
                raise AlicectlError(f"device login failed (HTTP {resp.status_code}): {error or resp.text[:300]}")
    raise AlicectlError("device login timed out")


async def x_token_code(x_token: str) -> str:
    data = await post_value(
        f"{PASSPORT}/1/bundle/auth/oauth/code_for_am/", QUASAR_CLIENT, "code", {"Authorization": f"OAuth {x_token}"}
    )
    log.info(f"xTokenCode issued (expires in ~{data.get('expires_in', '?')}s)")
    return data["code"]


async def oauth_token(x_token: str) -> tuple[str, datetime | None]:
    form = {**MUSIC_CLIENT, "grant_type": "x-token", "access_token": x_token}
    data = await post_value(OAUTH_URL, form, "access_token")
    lifetime = data.get("expires_in")
    return data["access_token"], datetime.now(UTC) + timedelta(seconds=lifetime) if lifetime else None
