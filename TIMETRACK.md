# TimeTrack

A lightweight, **cross-platform (Linux + Windows)** automatic time &
activity tracker — a privacy-first, self-hosted alternative to tools like
**DeskTime**. It automatically records which application/window you're using,
detects idle time, classifies activity as productive / unproductive / neutral,
and shows it all in a clean local web dashboard.

**All data stays on your machine** in a local SQLite database. Nothing is
uploaded anywhere.

---

## Features

- **Automatic tracking** — samples the focused window + idle state on a timer;
  no manual start/stop.
- **Cross-platform** — Windows (Win32 API) and Linux (X11 via `xdotool`/`xprop`);
  degrades gracefully elsewhere.
- **Idle detection** — time with no keyboard/mouse input past a threshold is
  recorded as idle.
- **Productivity model** — DeskTime-style productive / unproductive / neutral
  categories with configurable, mergeable rules.
- **Web dashboard** — productivity ring, active/idle/productive breakdown,
  top applications, and per-day navigation.
- **JSON API** — `/api/summary` and `/api/timeline` for your own tooling.
- **Compact storage** — contiguous same-activity spans are merged into a single
  row.
- **Zero cloud dependencies** — local SQLite only.

## Requirements

- Python 3.11+ (uses the stdlib `tomllib`).
- **Linux:** an X11 session and `xdotool` (recommended) or `xprop` for window
  titles, plus `xprintidle` for idle detection:
  ```bash
  sudo apt install xdotool x11-utils xprintidle   # Debian/Ubuntu
  ```
- **Windows:** works out of the box via `ctypes`; `pip install pywin32` gives
  slightly richer process info.

## Install

```bash
pip install -r requirements.txt
```

## Usage

Run the tracker (leave it running in the background / a startup service):

```bash
python -m timetrack track
```

In another terminal, launch the dashboard and open <http://127.0.0.1:8000>:

```bash
python -m timetrack serve
```

Print a text report for a day:

```bash
python -m timetrack report            # today
python -m timetrack report 2026-07-13 # a specific day
```

Check what the tracker can see on your machine:

```bash
python -m timetrack doctor
```

## Configuration

TimeTrack works with no config. To customize, copy the example and edit it:

```bash
cp config.example.toml config.toml
```

TimeTrack looks for `./config.toml`, then
`~/.config/timetrack/config.toml`, or pass one explicitly:

```bash
python -m timetrack -c /path/to/config.toml track
```

Key options (all optional):

| Key              | Default                                   | Meaning                                        |
| ---------------- | ----------------------------------------- | ---------------------------------------------- |
| `db_path`        | `~/.local/share/timetrack/timetrack.db`   | SQLite database location                       |
| `poll_interval`  | `5`                                       | Seconds between samples                        |
| `idle_threshold` | `180`                                     | Seconds of no input before counting as idle    |
| `host` / `port`  | `127.0.0.1` / `8000`                      | Dashboard bind address                         |
| `[rules]`        | built-in defaults                         | Substring rules per category (merged with defaults) |

Rules are case-insensitive substrings matched against `"<app> <window title>"`.
Unproductive matches win ties (e.g. YouTube open inside an otherwise
"productive" browser is counted as unproductive).

## Running the tracker at login

- **Linux (systemd user service):**
  ```ini
  # ~/.config/systemd/user/timetrack.service
  [Unit]
  Description=TimeTrack activity tracker
  [Service]
  ExecStart=%h/.local/bin/python -m timetrack track
  Restart=on-failure
  [Install]
  WantedBy=default.target
  ```
  ```bash
  systemctl --user enable --now timetrack
  ```
- **Windows:** add `python -m timetrack track` as a Task Scheduler task that
  runs at logon (or drop a shortcut in the Startup folder).

## Architecture

```
timetrack/
  platform/        OS abstraction: active window + idle seconds
    windows.py       Win32 (ctypes / pywin32)
    linux.py         X11 (xdotool / xprop / xprintidle)
    fallback.py      no-op backend for unsupported/headless hosts
  config.py        TOML config + categorization rules
  storage.py       SQLite persistence (span merging)
  analytics.py     aggregation, productivity/effectiveness, timeline
  tracker.py       the sampling loop
  dashboard/       Flask app + templates + static assets
  __main__.py      CLI: track / serve / report / doctor
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

## Privacy

TimeTrack stores window titles locally. Titles can contain sensitive text
(document names, chat subjects). The database lives under your home directory
and is never transmitted. Delete `db_path` to wipe your history.
