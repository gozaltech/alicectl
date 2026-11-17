import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx2

from ..errors import AlicectlError

type Form = dict[str, str]

UA = "com.yandex.iot/26.18.0 (Android 13)"
SECRETS = re.compile(r'"(\w*(?:token|Token|code|secret|password|Session_id|sessionid2)\w*)"\s*:\s*"[^"]*"')

log = logging.getLogger(__name__)
_http: ContextVar[httpx2.AsyncClient] = ContextVar("http")


@asynccontextmanager
async def http_client() -> AsyncIterator[httpx2.AsyncClient]:
    async with httpx2.AsyncClient(headers={"User-Agent": UA}, timeout=20) as client:
        token = _http.set(client)
        try:
            yield client
        finally:
            _http.reset(token)


def log_response(resp: httpx2.Response) -> None:
    log.debug(f"<- {resp.request.method} {resp.url.copy_with(query=None)} HTTP {resp.status_code}: {redact(resp.text)}")


def redact(text: str) -> str:
    return SECRETS.sub(r'"\1":"<hidden>"', text[:500])


async def request(method: str, url: str, **kwargs) -> httpx2.Response:
    resp = await _http.get().request(method, url, **kwargs)
    log_response(resp)
    return resp


async def post(url: str, **kwargs) -> httpx2.Response:
    return await request("POST", url, **kwargs)


def json_dict(resp: httpx2.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def post_value(url: str, form: Form, key: str, headers: Form | None = None) -> dict:
    resp = await post(url, data=form, headers=headers)
    if not (data := json_dict(resp)).get(key):
        raise AlicectlError(f"{url}: no {key} (HTTP {resp.status_code}): {resp.text[:300]}")
    return data
