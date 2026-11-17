from collections.abc import Callable, Iterable

from .errors import AlicectlError

ALL = "all"


def starts(key: str, wanted: str) -> bool:
    key = key.casefold()
    return key.startswith(wanted) or any(word.startswith(wanted) for word in key.split())


def pick[T](items: Iterable[T], query: str, keys: Callable[[T], Iterable[str]], what: str) -> T:
    wanted, items = query.strip().casefold(), list(items)
    found = [item for item in items if wanted in {key.casefold() for key in keys(item) if key}]
    found = found or [item for item in items if any(key and starts(key, wanted) for key in keys(item))]
    match found:
        case [item]:
            return item
        case []:
            raise AlicectlError(f"no {what} matches {query.strip()!r}")
        case _:
            names = ", ".join(sorted({[*keys(item)][-1] for item in found}))
            raise AlicectlError(f"{query.strip()!r} matches {len(found)} {what}s: {names}; be more specific")


def pick_many[T](items: list[T], query: str | None, keys: Callable[[T], Iterable[str]], what: str) -> list[T]:
    if not query or query.casefold() == ALL:
        return items
    return [pick(items, part, keys, what) for part in query.split(",")]
