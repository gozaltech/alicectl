import math
import struct
from enum import IntEnum
from string import hexdigits
from typing import TYPE_CHECKING

from ..errors import AlicectlError

if TYPE_CHECKING:
    from ..options import WifiOptions


class WifiType(IntEnum):
    OPEN = 1
    WEP = 2
    WPA = 3
    SSID_HASH = 4
    NONE = 5


SECURITY_CHOICES = ["open", "wep", "wpa", "none"]


def java_hash(text: str) -> int:
    h = 0
    for (unit,) in struct.iter_unpack(">H", text.encode("utf-16-be")):
        h = (31 * h + unit) & 0xFFFFFFFF
    return h - (1 << 32) if h & 0x80000000 else h


def length_prefixed(value: str, limit: int, what: str) -> bytes:
    raw = value.encode()
    if len(raw) > limit:
        raise AlicectlError(f"{what} longer than {limit} bytes")
    return bytes([len(raw)]) + raw


def wifi_error(ssid: str, password: str, security: str) -> str | None:
    if security == "none":
        return None
    if not ssid:
        return "--ssid is required"
    if len(ssid.encode()) > 32:
        return "SSID is longer than 32 bytes"
    size = len(password.encode())
    match security:
        case "open" if password:
            return "open networks take no password: clear --password / WIFI_PASSWORD or pick --security wpa/wep"
        case "wpa" if not 8 <= size <= 63:
            return f"WPA password must be 8-63 characters, got {size} (use --security open for a network without one)"
        case "wep" if not (size in {5, 13} or (size in {10, 26} and all(c in hexdigits for c in password))):
            return "WEP key must be 5 or 13 characters, or 10 or 26 hex digits"
    return None


def config_payload(args: WifiOptions, code: str) -> bytes:
    kind = WifiType[args.security.upper()]
    if kind is not WifiType.NONE and not args.hidden:
        kind = WifiType.SSID_HASH
    out = bytes([kind - 1])
    match kind:
        case WifiType.NONE:
            pass
        case WifiType.SSID_HASH:
            out += struct.pack("<h", int(math.fmod(java_hash(args.ssid), 32767)))
            out += length_prefixed(args.password, 63, "password")
        case _:
            out += length_prefixed(args.ssid, 32, "SSID") + length_prefixed(args.password, 63, "password")
    return out + length_prefixed(code, 128, "xTokenCode")
