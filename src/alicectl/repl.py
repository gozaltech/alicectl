import asyncio
import logging
import os
import shlex
import sys
from collections.abc import Callable
from types import ModuleType

import httpx2

from .console import Complete, LineEditor
from .context import Context
from .errors import AlicectlError
from .lookup import pick
from .options import DEFAULT_STATION, Options, Parsed

PROMPT = "alicectl> "
EXIT_WORDS = {"exit", "quit"}
HELP_WORDS = {"help", "?"}
HISTORY_WORD = "history"
USE_WORD = "use"
CLEAR_WORDS = {"none", "-"}

type Parse = Callable[[list[str]], Parsed]


def set_debug(debug: bool) -> None:
    logging.getLogger(__package__).setLevel(logging.DEBUG if debug else logging.INFO)


def network_error(e: httpx2.HTTPError) -> str:
    return f"network error: {e!r}"


async def guarded(ctx: Context, command: Parsed) -> str | None:
    assert command.func is not None
    try:
        await command.func(ctx, command.options)
    except AlicectlError as e:
        return str(e) or None
    except httpx2.HTTPError as e:
        return network_error(e)
    return None


def station_words(ctx: Context) -> list[str]:
    names = [d.name for d in ctx.known_devices() if not d.is_app]
    return ["all", *(f'"{name}"' if " " in name else name for name in names)]


def matching(pool: list[str], word: str) -> list[str]:
    prefix = word.strip('"').casefold()
    return [w for w in pool if w.strip('"').casefold().startswith(prefix)]


def line_editing(candidates: Complete) -> ModuleType | None:
    try:
        import readline
    except ImportError:
        return None

    def complete(text: str, state: int) -> str | None:
        options = candidates(readline.get_line_buffer()[: readline.get_begidx()], text)
        return options[state] if state < len(options) else None

    readline.set_completer_delims(" \t")
    readline.set_completer(complete)
    readline.parse_and_bind("bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete")
    return readline


def windows_console() -> bool:
    return sys.platform == "win32" and sys.stdin.isatty() and sys.stdout.isatty()


class Reader:
    def __init__(self, commands: list[str], stations: Callable[[], list[str]]) -> None:
        def candidates(before: str, word: str) -> list[str]:
            return matching(stations() if before.strip() else commands, word)

        self.editor = LineEditor(candidates) if windows_console() else None
        self.readline = None if self.editor else line_editing(candidates)

    def read(self) -> str | None:
        while True:
            try:
                return (self.editor.read(PROMPT) if self.editor else input(PROMPT)).strip()
            except EOFError:
                print()
                return None
            except KeyboardInterrupt:
                if not self.editor:
                    print()

    def remember(self, line: str) -> None:
        if self.editor:
            self.editor.remember(line)
        elif self.readline and (length := self.readline.get_current_history_length()):
            self.readline.replace_history_item(length - 1, line)

    def close(self) -> None:
        if self.editor:
            self.editor.mode.restore()


def recall(line: str, history: list[str]) -> str | None:
    ref = line.removeprefix("!")
    if ref == "!" and history:
        return history[-1]
    if ref.isdigit() and 1 <= int(ref) <= len(history):
        return history[int(ref) - 1]
    print(f"{line}: no such command in this session (see '{HISTORY_WORD}')", file=sys.stderr)
    return None


def show_history(history: list[str]) -> None:
    width = len(str(len(history)))
    for number, line in enumerate(history, 1):
        print(f"{number:>{width}}  {line}")


def next_words(reader: Reader, history: list[str]) -> list[str] | None:
    while (line := reader.read()) is not None:
        if line.startswith("!"):
            if not (line := recall(line, history)):
                continue
            print(line)
            reader.remember(line)
        if line == HISTORY_WORD:
            show_history(history)
            continue
        if line:
            history.append(line)
        try:
            if words := shlex.split(line):
                return words
        except ValueError as e:
            print(f"can't read that: {e}", file=sys.stderr)
    return None


async def use_station(ctx: Context, name: str) -> str:
    stations = [d for d in ctx.known_devices() or await ctx.devices() if not d.is_app]
    return pick(stations, name, lambda d: (d.id, d.name), "station").name


def choose_default(runner: asyncio.Runner, ctx: Context, words: list[str]) -> None:
    match words:
        case []:
            current = os.environ.get(DEFAULT_STATION)
            print(f"default station: {current}" if current else "no default station")
        case [word] if word.casefold() in CLEAR_WORDS:
            os.environ.pop(DEFAULT_STATION, None)
            print("no default station")
        case _:
            try:
                os.environ[DEFAULT_STATION] = runner.run(use_station(ctx, " ".join(words)))
            except AlicectlError as e:
                print(e, file=sys.stderr)
                return
            print(f"default station: {os.environ[DEFAULT_STATION]}")


def command_words(words: list[str]) -> list[str]:
    if words[0] in HELP_WORDS:
        return [*words[1:2], "--help"]
    return words


def repl(args: Options, parse: Parse, commands: list[str]) -> None:
    print("alicectl: type a command (e.g. status), help, history, use STATION, or exit")
    history: list[str] = []
    with asyncio.Runner() as runner:
        ctx = Context(args)
        runner.run(ctx.__aenter__())
        reader = Reader([*commands, USE_WORD], lambda: station_words(ctx))
        try:
            while (words := next_words(reader, history)) is not None:
                if words[0] in EXIT_WORDS:
                    break
                if words[0] == USE_WORD:
                    choose_default(runner, ctx, words[1:])
                    continue
                try:
                    command = parse(command_words(words))
                except SystemExit:
                    continue
                if command.func is None:
                    continue
                set_debug(command.options.debug or args.debug)
                ctx.use(command.options)
                try:
                    if error := runner.run(guarded(ctx, command)):
                        print(error, file=sys.stderr)
                except KeyboardInterrupt:
                    print("interrupted", file=sys.stderr)
                finally:
                    ctx.cache.save()
        finally:
            reader.close()
            runner.run(ctx.__aexit__(None, None, None))
