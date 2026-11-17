import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager

PROCESSED_INPUT, LINE_INPUT, ECHO_INPUT = 0x1, 0x2, 0x4
VT_INPUT = 0x200
SPECIAL_KEYS = {"H": "up", "P": "down", "K": "left", "M": "right", "G": "home", "O": "end", "S": "delete"}
VT_KEYS = {"A": "up", "B": "down", "D": "left", "C": "right", "H": "home", "F": "end", "3": "delete"}
CONTROL_KEYS = {"\r": "enter", "\n": "enter", "\x08": "backspace", "\t": "tab", "\x03": "interrupt", "\x1a": "eof"}
CONTROL_KEYS |= {"\x04": "eof", "\x1b": "escape"}

type Complete = Callable[[str, str], list[str]]


class ConsoleMode:
    def __init__(self) -> None:
        self.set: Callable[[int], object] | None = None
        self.original = 0
        if sys.platform != "win32" or not sys.stdin.isatty():
            return
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32")
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(sys.stdin.fileno()))
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            self.original = mode.value
            self.set = lambda value: kernel32.SetConsoleMode(handle, wintypes.DWORD(value))

    @contextmanager
    def keys(self) -> Iterator[None]:
        if self.set:
            self.set(self.original & ~(VT_INPUT | PROCESSED_INPUT | LINE_INPUT | ECHO_INPUT))
        try:
            yield
        finally:
            if self.set:
                self.set(self.original & ~VT_INPUT | PROCESSED_INPUT | LINE_INPUT | ECHO_INPUT)

    def restore(self) -> None:
        if self.set:
            self.set(self.original)


def common_prefix(words: list[str]) -> str:
    first, last = min(words), max(words)
    size = next((i for i, (a, b) in enumerate(zip(first, last, strict=False)) if a != b), min(len(first), len(last)))
    return first[:size]


class LineEditor:
    def __init__(self, complete: Complete) -> None:
        self.complete = complete
        self.history: list[str] = []
        self.mode = ConsoleMode()
        self.prompt = ""
        self.text = ""
        self.pending: list[str] = []
        self.cursor = 0

    @staticmethod
    def write(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def key(self) -> str:
        if sys.platform != "win32":
            raise OSError("this line editor needs a Windows console")
        import msvcrt

        match ch := self.pending.pop(0) if self.pending else msvcrt.getwch():
            case "\x00" | "\xe0":
                return SPECIAL_KEYS.get(msvcrt.getwch(), "")
            case "\x1b" if msvcrt.kbhit():
                if (following := msvcrt.getwch()) != "[":
                    self.pending.append(following)
                    return "escape"
                code = msvcrt.getwch()
                if code == "3":
                    msvcrt.getwch()
                return VT_KEYS.get(code, "")
        return CONTROL_KEYS.get(ch, ch)

    def move_to(self, position: int) -> None:
        if position < self.cursor:
            self.write("\b" * (self.cursor - position))
        else:
            self.write(self.text[self.cursor : position])
        self.cursor = position

    def edit(self, start: int, end: int, insert: str) -> None:
        self.move_to(start)
        tail = self.text[end:]
        removed = (end - start) - len(insert)
        self.text = self.text[:start] + insert + tail
        self.write(insert + tail + " " * max(removed, 0) + "\b" * (len(tail) + max(removed, 0)))
        self.cursor = start + len(insert)

    def replace(self, text: str) -> None:
        self.edit(0, len(self.text), text)

    def tab(self) -> None:
        before = self.text[: self.cursor]
        word = before.split(" ")[-1]
        options = self.complete(before[: len(before) - len(word)], word)
        if len(options) == 1:
            self.edit(self.cursor - len(word), self.cursor, options[0] + " ")
        elif options and len(prefix := common_prefix(options)) > len(word):
            self.edit(self.cursor - len(word), self.cursor, prefix)
        elif options:
            back = "\b" * (len(self.text) - self.cursor)
            self.write(f"\r\n{'  '.join(options)}\r\n{self.prompt}{self.text}{back}")

    def read(self, prompt: str) -> str:
        self.prompt, self.text, self.cursor = prompt, "", 0
        browsing, draft = len(self.history), ""
        self.write(prompt)
        with self.mode.keys():
            while True:
                match self.key():
                    case "enter":
                        self.write("\r\n")
                        if self.text and self.text != (self.history or [""])[-1]:
                            self.history.append(self.text)
                        return self.text
                    case "interrupt":
                        self.write("\r\n")
                        raise KeyboardInterrupt
                    case "eof" if not self.text:
                        raise EOFError
                    case "up" if browsing > 0:
                        draft = self.text if browsing == len(self.history) else draft
                        browsing -= 1
                        self.replace(self.history[browsing])
                    case "down" if browsing < len(self.history):
                        browsing += 1
                        self.replace(self.history[browsing] if browsing < len(self.history) else draft)
                    case "left" if self.cursor > 0:
                        self.move_to(self.cursor - 1)
                    case "right" if self.cursor < len(self.text):
                        self.move_to(self.cursor + 1)
                    case "home":
                        self.move_to(0)
                    case "end":
                        self.move_to(len(self.text))
                    case "backspace" if self.cursor > 0:
                        self.edit(self.cursor - 1, self.cursor, "")
                    case "delete" if self.cursor < len(self.text):
                        self.edit(self.cursor, self.cursor + 1, "")
                    case "escape":
                        self.replace("")
                    case "tab":
                        self.tab()
                    case str(ch) if len(ch) == 1 and ch.isprintable():
                        self.edit(self.cursor, self.cursor, ch)

    def remember(self, line: str) -> None:
        if self.history:
            self.history[-1] = line
        else:
            self.history.append(line)
