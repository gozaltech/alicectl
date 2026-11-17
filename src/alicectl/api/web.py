import json
import logging
import re
from typing import Self

import httpx2

from ..config import SESSION_FILE, account_id, write_private
from ..errors import AlicectlError
from .auth import PASSPORT
from .http import json_dict, log_response, post_value

log = logging.getLogger(__name__)

RPC_URL = "https://rpc.alice.yandex.ru/gproxy"
RPC_APP_ID = "unknown"
CSRF_PAGE = "https://yandex.ru/iot"
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class WebSession:
    def __init__(self, x_token: str) -> None:
        self.x_token = x_token
        self.account = account_id(x_token)
        self.http = httpx2.AsyncClient(headers={"User-Agent": WEB_UA}, timeout=20, follow_redirects=True)
        self.csrf: str | None = None
        self.changed = False

    async def __aenter__(self) -> Self:
        self.load()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.changed and self.signed_in:
            self.save()
        await self.http.aclose()

    def load(self) -> None:
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            if data["account"] != self.account:
                return
            for cookie in data["cookies"]:
                self.http.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
            self.csrf = data.get("csrf")
        except FileNotFoundError, ValueError, KeyError, TypeError:
            self.http.cookies.clear()

    def save(self) -> None:
        cookies = [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in self.http.cookies.jar
        ]
        write_private(SESSION_FILE, json.dumps({"account": self.account, "cookies": cookies, "csrf": self.csrf}))

    @property
    def signed_in(self) -> bool:
        return any(cookie.name == "Session_id" for cookie in self.http.cookies.jar)

    async def sign_in(self) -> None:
        self.http.cookies.clear()
        self.csrf = None
        form = {"type": "x-token", "retpath": "https://www.yandex.ru"}
        bundle = await post_value(
            f"{PASSPORT}/1/bundle/auth/x_token/",
            form,
            "track_id",
            {"Ya-Consumer-Authorization": f"OAuth {self.x_token}"},
        )
        await self.http.get(f"{bundle['passport_host']}/auth/session/", params={"track_id": bundle["track_id"]})
        if not self.signed_in:
            raise AlicectlError("could not open a Yandex web session with the master token")
        self.changed = True
        log.debug("opened a new Yandex web session")

    async def csrf_token(self) -> str:
        if self.csrf:
            return self.csrf
        page = await self.http.get(CSRF_PAGE)
        if not (match := re.search(r'"csrfToken2":"(.+?)"', page.text)):
            raise AlicectlError(f"no CSRF token on {CSRF_PAGE}")
        self.csrf, self.changed = match[1], True
        return match[1]

    async def send(self, method: str, url: str, csrf: bool, headers: dict | None = None, **kwargs) -> httpx2.Response:
        for retry in (False, True):
            if not self.signed_in:
                await self.sign_in()
            extra = {"x-csrf-token": await self.csrf_token()} if csrf and method != "GET" else {}
            resp = await self.http.request(method, url, headers={**(headers or {}), **extra}, **kwargs)
            log_response(resp)
            if retry:
                break
            if resp.status_code == 401 or resp.url.host.startswith("passport."):
                log.debug("web session expired")
                self.http.cookies.clear()
            elif resp.status_code == 403 and extra:
                log.debug("CSRF token rejected")
                self.csrf = None
            else:
                break
        return resp

    async def request(self, method: str, url: str, **kwargs) -> dict:
        resp = await self.send(method, url, csrf=True, **kwargs)
        if (data := json_dict(resp)).get("status") != "ok":
            reason = data.get("message") or data.get("code") or resp.text[:300]
            raise AlicectlError(f"{method} {url} failed (HTTP {resp.status_code}): {reason}")
        return data

    def app_headers(self) -> dict[str, str]:
        device_uuid = self.account.ljust(32, "0")
        app = {"app_id": RPC_APP_ID, "uuid": device_uuid, "lang": "ru"}
        return {
            "Accept": "application/json",
            "x-ya-app-id": RPC_APP_ID,
            "x-ya-uuid": device_uuid,
            "x-ya-language": "ru",
            "x-ya-app-type": "iot-app",
            "x-ya-application": json.dumps(app),
        }

    async def rpc(self, method: str, path: str, body: dict | None = None, experiments: tuple[str, ...] = ()) -> dict:
        url = RPC_URL + path
        headers = self.app_headers() | ({"x-ya-experiments": json.dumps(list(experiments))} if experiments else {})
        resp = await self.send(method, url, csrf=False, headers=headers, json=body)
        data = json_dict(resp)
        if resp.status_code != 200:
            raise AlicectlError(f"{path} failed (HTTP {resp.status_code}): {data.get('Message') or resp.text[:300]}")
        return data
