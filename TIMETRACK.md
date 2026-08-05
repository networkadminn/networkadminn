# TimeTrack

A **cross-platform (Windows / macOS / Linux)** automatic time & activity
tracker — a self-hosted alternative to tools like **DeskTime**.

TimeTrack comes in **two modes**:

1. **Local mode** — a single-machine tracker + personal dashboard. No server,
   data stays entirely on your machine (great for freelancers / personal use).
2. **Team mode** — an **employee agent** on each computer that tracks activity
   and captures screenshots and uploads them to a central **server** with:
   - an **admin dashboard** (all employees, screenshots, activity, idle time,
     charts, team overview, online status), and
   - a **user dashboard** (each employee sees only their own time + charts).

---

## Team mode architecture

```
 Employee machines                         Central server
 ┌───────────────────────┐   HTTPS/HTTP   ┌────────────────────────────┐
 │ timetrack.agent        │ ─────────────▶ │ timetrack.server (Flask)   │
 │  • active window       │  Bearer token  │  • auth + roles            │
 │  • idle detection      │  activities +  │  • ingest API              │
 │  • screenshots (mss)   │  screenshots   │  • admin dashboard         │
 │  • local buffer (SQLite)│               │  • per-user dashboard      │
 │  • offline-safe resync │                │  • charts (Chart.js)       │
 └───────────────────────┘                └────────────────────────────┘
```

- The agent **buffers locally** and keeps working when the network/server is
  down, then resyncs — no data loss.
- Auth uses **username/password sessions** (Flask-Login) for dashboards and a
  per-employee **API token** for the agent.
- Screenshots are stored on the server; only the **owner or an admin** can view
  a given screenshot.

## Features

- **Automatic tracking** — samples the focused window + idle state on a timer;
  no manual start/stop.
- **Cross-platform** — Windows (Win32), macOS (Quartz/AppleScript), Linux (X11
  via `xdotool`/`xprop`); degrades gracefully elsewhere.
- **Screenshots** — periodic desktop capture (via `mss`) with server-side
  thumbnails.
- **Idle detection** — time with no keyboard/mouse input past a threshold is
  recorded as idle.
- **Productivity model** — DeskTime-style productive / unproductive / neutral
  categories with configurable, mergeable rules.
- **Dashboards** — productivity ring, hourly activity chart, category split,
  top-apps chart, screenshot gallery, team overview with online status.
- **Roles** — `admin` (sees everyone) and `employee` (sees only themselves).
- **Compact storage** — contiguous same-activity spans are merged.

## Requirements

- Python 3.11+ (uses the stdlib `tomllib`).
- **Linux:** an X11 session and `xdotool` (recommended) or `xprop` for window
  titles, plus `xprintidle` for idle detection:
  ```bash
  sudo apt install xdotool x11-utils xprintidle   # Debian/Ubuntu
  ```
- **Windows:** works out of the box via `ctypes`; `pip install pywin32` gives
  slightly richer process info.
- **macOS:** `pip install pyobjc-framework-Quartz` for window/idle detection
  (falls back to AppleScript/`ioreg`). Grant the terminal/app **Screen
  Recording** and **Accessibility** permissions for titles + screenshots.

## Install

```bash
pip install -r requirements.txt
```

## Usage — Local mode (single machine)

Run the tracker (leave it running in the background / a startup service):

```bash
python -m timetrack track
```

In another terminal, launch the personal dashboard and open <http://127.0.0.1:8000>:

```bash
python -m timetrack serve
```

Other local commands:

```bash
python -m timetrack report            # today's text report
python -m timetrack report 2026-07-13 # a specific day
python -m timetrack doctor            # check what the tracker can see
```

## Usage — Team mode (server + employee agents)

### 1. On the server

```bash
# Create an admin and one or more employees (prints each API token).
python -m timetrack.server create-user boss  --admin --name "The Boss"
python -m timetrack.server create-user alice --name "Alice"
python -m timetrack.server list-users        # view users + API tokens

# Start the server (listens on 0.0.0.0:8080 by default).
python -m timetrack.server run
```

Open <http://SERVER:8080/> and sign in:
- **Admins** land on the **Team** dashboard (all employees, charts, screenshots).
- **Employees** land on **My activity** (their own stats only).

Server data (SQLite DB, screenshots, secret key) lives under
`~/.local/share/timetrack-server/` (override with `TIMETRACK_SERVER_DATA`).

### 2. On each employee machine

```bash
cp agent.example.toml agent.toml     # set server_url + api_token
python -m timetrack.agent ping       # verify server + token
python -m timetrack.agent run        # start tracking + screenshots + sync
python -m timetrack.agent status     # show buffered/pending items
```

You can also configure the agent purely via environment variables:

```bash
export TIMETRACK_SERVER_URL="http://your-server:8080"
export TIMETRACK_API_TOKEN="the-employees-token"
python -m timetrack.agent run
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

## Running at login

Replace `track` with `agent run` for team mode.

- **Linux (systemd user service):**
  ```ini
  # ~/.config/systemd/user/timetrack.service
  [Unit]
  Description=TimeTrack activity tracker
  [Service]
  ExecStart=%h/.local/bin/python -m timetrack track   # or: -m timetrack.agent run
  Restart=on-failure
  [Install]
  WantedBy=default.target
  ```
  ```bash
  systemctl --user enable --now timetrack
  ```
- **Windows:** add `python -m timetrack track` (or `python -m timetrack.agent
  run`) as a Task Scheduler task that runs at logon.
- **macOS:** create a `launchd` LaunchAgent plist in `~/Library/LaunchAgents`
  that runs the same command at login.

## Architecture

```
timetrack/
  platform/        OS abstraction: active window + idle + screenshots
    windows.py       Win32 (ctypes / pywin32)
    macos.py         Quartz / AppleScript / ioreg
    linux.py         X11 (xdotool / xprop / xprintidle)
    fallback.py      no-op backend for unsupported/headless hosts
    screenshot.py    cross-platform capture (mss + Pillow)
  config.py        TOML config + categorization rules (shared)
  storage.py       local SQLite persistence (span merging)
  analytics.py     aggregation, productivity/effectiveness, timeline
  tracker.py       local-mode sampling loop
  dashboard/       local-mode personal Flask dashboard
  __main__.py      local CLI: track / serve / report / doctor

  agent/           TEAM MODE: employee-side agent
    config.py        agent config (server_url, token, intervals, rules)
    buffer.py        offline-safe local SQLite queue
    client.py        stdlib HTTP client (activities + multipart screenshots)
    agent.py         sample + capture + buffer + sync loop
    __main__.py      agent CLI: run / ping / status / flush

  server/          TEAM MODE: central multi-user server
    models.py        User / Activity / Screenshot (SQLAlchemy)
    auth.py          login/logout + role guards (Flask-Login)
    api.py           token-authenticated ingest API (/api/v1/*)
    views.py         admin + user dashboards, screenshot serving
    app.py           application factory
    __main__.py      server CLI: run / create-user / list-users / ...
    templates/, static/
```

### Server API (for the agent)

| Method & path              | Auth            | Purpose                          |
| -------------------------- | --------------- | -------------------------------- |
| `GET  /api/v1/ping`        | Bearer token    | Verify token / identify user     |
| `POST /api/v1/activities`  | Bearer token    | Batch upload activity spans      |
| `POST /api/v1/screenshots` | Bearer token    | Upload one screenshot (multipart)|

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

## Privacy & security notes

- **Local mode** keeps everything on the machine; nothing is transmitted.
- **Team mode** uploads activity, window titles and screenshots to your server.
  Titles/screenshots can contain sensitive information, so:
  - Run the server behind **HTTPS** (e.g. a reverse proxy) in production.
  - Screenshots are access-controlled (owner or admin only); the DB and image
    files live under the server's data dir.
  - Set `screenshots_enabled = false` in `agent.toml` to disable capture.
  - API tokens are per-employee and can be rotated with
    `python -m timetrack.server reset-token <username>`.
- Be sure your monitoring complies with local laws and that employees are
  informed.

## Web security hardening

The server (and the local dashboard) apply defence-in-depth controls **in the
application itself**, so they work regardless of the front-end web server
(Apache/cPanel, Nginx, gunicorn, ...). The equivalent per-site Apache rules are
also provided in [`deploy/apache-security.htaccess`](deploy/apache-security.htaccess)
for reseller/cPanel deployments that prefer to set headers at the web-server layer.

### Response security headers (enabled by default)

Every response carries:

| Header | Default |
| ------ | ------- |
| `Content-Security-Policy` | `default-src 'self'` + a per-request script **nonce** (no `'unsafe-inline'` scripts); Chart.js CDN allow-listed |
| `Strict-Transport-Security` (HSTS) | `max-age=31536000; includeSubDomains` — sent only over HTTPS |
| `X-Frame-Options` | `SAMEORIGIN` (clickjacking) |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Cross-Origin-Opener-Policy` / `Cross-Origin-Resource-Policy` | `same-origin` |

Session cookies are `HttpOnly` + `SameSite=Lax` (and `Secure` when HSTS is forced).

Toggle / tune via environment variables:

| Env var | Default | Meaning |
| ------- | ------- | ------- |
| `TIMETRACK_SECURITY_HEADERS` | `true` | Master on/off switch for all headers |
| `TIMETRACK_HSTS` | `true` | Send HSTS on HTTPS responses |
| `TIMETRACK_HSTS_FORCE` | `false` | Send HSTS + mark cookies `Secure` even if HTTPS isn't detected (TLS-terminating proxy) |
| `TIMETRACK_TRUST_PROXY` | `false` | Trust `X-Forwarded-For`/`-Proto` from a proxy you control |
| `TIMETRACK_ADMIN_IP_ALLOWLIST` | *(empty)* | Comma-separated IPs/CIDRs allowed to reach the admin dashboard |

### Authentication & access control

- **Strong password policy** (min 12 chars, mixed case, digit, symbol, not a
  common/guessable value, not the username) is enforced when provisioning:
  ```bash
  python -m timetrack.server create-user alice --name "Alice"
  python -m timetrack.server set-password alice
  # add --allow-weak-password to bypass (e.g. automated seeding)
  ```
- **Two-factor authentication (TOTP MFA)** for dashboard logins:
  ```bash
  python -m timetrack.server enable-mfa alice    # prints secret + otpauth:// URI
  python -m timetrack.server disable-mfa alice
  ```
  Once enabled the user must enter a 6-digit code from an authenticator app
  (Google Authenticator, Authy, 1Password, ...) after their password.
- **Admin IP allow-list** restricts the `/admin` dashboard by source IP/CIDR:
  ```bash
  export TIMETRACK_ADMIN_IP_ALLOWLIST="203.0.113.4,10.0.0.0/24"
  python -m timetrack.server run
  ```

### Application-code protections (already in place)

- **SQL injection:** all database access uses the SQLAlchemy ORM (server) and
  fully **parameterized** SQLite queries (local storage) — no string-built SQL.
- **XSS:** templates are rendered with Jinja2 **auto-escaping**; dynamic data
  injected into inline scripts is emitted via `|tojson` and the scripts run
  under a CSP nonce.
- **WAF:** a Web Application Firewall (e.g. cPanel/WHM **ModSecurity**) is a
  server-level control and should be enabled by your host/reseller in addition
  to the above.
