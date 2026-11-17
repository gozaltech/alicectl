# alicectl

Set up and control Yandex Stations and your Alice smart home from the command line, without the
«Дом с Алисой» app.

## Features

- **Setup:** Wi-Fi and account binding over sound or Bluetooth LE, with name and room in 1 step
- **Music:** Yandex Music search, likes, your playlists, My Wave; skip N tracks, seek, like and dislike
- **Control:** make stations speak, send Alice commands, volume, timers (optionally silent), status
- **Local first:** commands go straight to the station over your network, with the cloud as a fallback
- **Alarms and reminders**, waking with a track or a built-in melody
- **Settings:** account-wide and per station, e.g. wake word, do not disturb, content filters
- **Smart home:** devices, on/off, brightness, temperature, modes, scenarios
- **Interactive mode** with history and Tab completion
- **Login** by confirming a code at ya.ru/device; no passwords or cookies to copy

## Install

Needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv tool install --editable .  # optional: puts alicectl on PATH
```

## Quick start

```sh
alicectl login                         # confirm the code at ya.ru/device
alicectl ble --name Кухня --room Кухня  # station in setup mode; or: alicectl sound
alicectl status -s all
alicectl play Queen Bohemian Rhapsody
alicectl next 3
alicectl timer 10
alicectl alarm add 07:30 --weekdays
alicectl                               # interactive mode
```

`alicectl --help` lists the commands and `alicectl COMMAND --help` their options.

## Configuration

Optional `.env` in the project folder:

```ini
# default station for -s
ALICECTL_STATION=Кухня
# Wi-Fi for setup
WIFI_SSID=Home
WIFI_PASSWORD=secret123
```

`-s` takes a station name, the start of a name, an ID, several separated by commas, or `all`.

## Private files

`.ya_token`, `.ya_session` and `.ya_cache` hold tokens for your Yandex account. They are in `.gitignore`; don't
share them.

## Contributing

Suggestions, bug reports and pull requests are welcome.

- **Ideas and bugs:** open an [issue](https://github.com/gozaltech/alicectl/issues). For a bug, include the command,
  its output and your station model; `--debug` shows more detail. Remove tokens from anything you paste.
- **Pull requests:** keep each one to a single change, and run `uv run ruff check`, `uv run ruff format` and
  `uv run pyright` before opening it.

## License

[MIT](https://opensource.org/license/mit), © 2026 gozaltech.
