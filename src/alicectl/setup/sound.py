import math
import shutil
import struct
import subprocess
import sys
import wave
import zlib
from itertools import batched
from pathlib import Path

from ..errors import AlicectlError

RATE = 16000
SYMBOL = 640


def _gf_tables() -> tuple[list[int], list[int]]:
    exp, log_, x = [0] * 30, [0] * 16, 1
    for i in range(15):
        exp[i] = exp[i + 15] = x
        log_[x] = i
        x = (x << 1) ^ (0x13 if x & 8 else 0)
    return exp, log_


GF_EXP, GF_LOG = _gf_tables()


def gf_mul(a: int, b: int) -> int:
    return GF_EXP[GF_LOG[a] + GF_LOG[b]] if a and b else 0


def _rs_generator() -> list[int]:
    gen = [1]
    for i in range(1, 5):
        gen = [a ^ gf_mul(b, GF_EXP[i]) for a, b in zip([*gen, 0], [0, *gen], strict=True)]
    return gen


RS_GEN = _rs_generator()


def rs_parity(block: list[int]) -> list[int]:
    msg = block + [0] * 4
    for i in range(len(block)):
        if msg[i]:
            for j in range(1, 5):
                msg[i + j] ^= gf_mul(RS_GEN[j], msg[i])
    return msg[-4:]


def tones(payload: bytes) -> list[int]:
    payload += (zlib.crc32(payload) & 0xFFFF).to_bytes(2, "little")
    nibbles = [n for b in payload for n in (b >> 4, b & 0xF)]
    data = [n for block in batched(nibbles, 11, strict=False) for n in (*block, *rs_parity(list(block)))]
    return [6192, 6192, 5120, 5120, 1024, 1024] + [1024 + 256 * n for n in data] + [5632] * 8


def write_wav(path: Path, freqs: list[int], volume: float, pad: float = 0.3) -> float:
    silence = [0] * int(RATE * pad)
    amp = 32767 * max(0.0, min(volume, 1.0))
    pcm = silence + [int(amp * math.sin(2 * math.pi * f * k / RATE)) for f in freqs for k in range(SYMBOL)] + silence
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
    return len(pcm) / RATE


def play_wav(path: Path) -> None:
    players = (["paplay"], ["aplay", "-q"], ["afplay"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"])
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    if shutil.which("powershell.exe") and shutil.which("wslpath"):
        win = subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
        command = ["powershell.exe", "-NoProfile", "-Command", f"(New-Object Media.SoundPlayer '{win}').PlaySync()"]
    elif (command := next(([*cmd, str(path)] for cmd in players if shutil.which(cmd[0])), None)) is None:
        raise AlicectlError(f"no audio player found; play {path} manually")
    subprocess.run(command, check=True)
