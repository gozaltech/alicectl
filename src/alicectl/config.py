import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_DIR if (PROJECT_DIR / "pyproject.toml").is_file() else Path.cwd()
ENV_FILE = CONFIG_DIR / ".env"
TOKEN_FILE = CONFIG_DIR / ".ya_token"
SESSION_FILE = CONFIG_DIR / ".ya_session"
CACHE_FILE = CONFIG_DIR / ".ya_cache"
TOKEN_EXPIRY_MARGIN = timedelta(hours=1)


def write_private(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def account_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class Cache:
    def __init__(self, account: str) -> None:
        self.account = account
        self.entries: dict[str, dict] = {}
        self.changed = False
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if data["account"] == account:
                self.entries = dict(data["entries"])
        except FileNotFoundError, ValueError, KeyError, TypeError:
            pass

    def get(self, key: str) -> Any:
        entry = self.entries.get(key) or {}
        if (expires := entry.get("expires_at")) and datetime.fromisoformat(expires) <= datetime.now(UTC):
            return None
        return entry.get("value")

    def put(self, key: str, value: Any, expires_at: datetime | None = None) -> None:
        self.entries[key] = {
            "value": value,
            "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None,
        }
        self.changed = True

    def drop(self, key: str) -> None:
        if self.entries.pop(key, None) is not None:
            self.changed = True

    def save(self) -> None:
        if self.changed:
            write_private(CACHE_FILE, json.dumps({"account": self.account, "entries": self.entries}))
            self.changed = False


@dataclass(slots=True)
class MasterToken:
    x_token: str
    expires_at: datetime | None = None

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and datetime.now(UTC) >= self.expires_at - TOKEN_EXPIRY_MARGIN


def load_token() -> MasterToken | None:
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        expires_at = data.get("expires_at")
        return MasterToken(data["x_token"], datetime.fromisoformat(expires_at) if expires_at else None)
    except FileNotFoundError, ValueError, KeyError, TypeError:
        return None


def save_token(token: MasterToken) -> None:
    expires_at = token.expires_at.isoformat(timespec="seconds") if token.expires_at else None
    write_private(TOKEN_FILE, json.dumps({"x_token": token.x_token, "expires_at": expires_at}, indent=2))
    log.info(f"saved master token to {TOKEN_FILE} (expires {expires_at or 'unknown'})")
