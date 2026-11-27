# ESS Tracker — Docker Hub guide

Self-host the **web server** (UI + agent API). Desktop agents still install separately (`.deb` / Windows).

**Image:** [`euclidee/esstracker-server`](https://hub.docker.com/r/euclidee/esstracker-server)  
**Tags:** `latest`, `0.3.19` (and later version tags)

---

## 1. Pull and run (recommended)

```bash
docker pull euclidee/esstracker-server:latest

docker run -d \
  --name esstracker \
  --restart unless-stopped \
  -p 8080:8080 \
  -v esstracker-data:/data \
  -e ESSTRACKER_PUBLIC_URL=http://localhost:8080 \
  -e TIMETRACK_SERVER_DATA=/data \
  -e PORT=8080 \
  euclidee/esstracker-server:latest
```

Open **http://localhost:8080**

1. First empty volume → **/setup** (company brand + first admin).
2. Sign in → dashboard.
3. Point desktop agents at this URL (e.g. `http://YOUR_LAN_IP:8080`).

### Useful commands

```bash
docker logs -f esstracker
docker stop esstracker
docker start esstracker
docker rm -f esstracker          # remove container (keeps volume)
docker volume rm esstracker-data  # wipe DB / screenshots / setup (full reset)
```

---

## 2. Docker Compose

Save as `docker-compose.yml`:

```yaml
services:
  tracker:
    image: euclidee/esstracker-server:latest
    ports:
      - "8080:8080"
    environment:
      TIMETRACK_SERVER_DATA: /data
      ESSTRACKER_PUBLIC_URL: http://localhost:8080
      PORT: "8080"
      # Optional — see env table below
      # TIMETRACK_SECURE_COOKIES: "1"
      # TIMETRACK_SECRET_KEY: "replace-with-long-random-string"
      # GEMINI_API_KEY: ""
    volumes:
      - esstracker-data:/data
    restart: unless-stopped

volumes:
  esstracker-data:
```

```bash
docker compose up -d
docker compose logs -f
docker compose down
```

---

## 3. Environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TIMETRACK_SERVER_DATA` | Yes (keep `/data`) | `/data` | Persistent data dir inside the container (DB, screenshots, logo, secret). **Mount a volume here.** |
| `PORT` | No | `8080` | Port gunicorn listens on **inside** the container. Map with `-p HOST:PORT`. |
| `ESSTRACKER_PUBLIC_URL` | **Strongly recommended** | `http://localhost:8080` | Public base URL shown in emails / download links. Use your real host, e.g. `https://tracker.example.com` or `http://192.168.1.10:8080`. |
| `TIMETRACK_PUBLIC_URL` | No | — | Alias for `ESSTRACKER_PUBLIC_URL`. |
| `TIMETRACK_SECRET_KEY` | No | auto-file in `/data/secret_key` | Flask session secret. Leave empty to auto-generate and persist on the volume. Set explicitly only if you need multi-replica sticky sessions. |
| `TIMETRACK_SECURE_COOKIES` | No | off | Set to `1` behind **HTTPS** so session cookies are `Secure`. |
| `TIMETRACK_EXPECTED_HOURS` | No | `8` | Default expected workday hours (overridable in Company settings). |
| `TIMETRACK_EXPECTED_ARRIVAL` | No | `9.5` | Default expected arrival hour (9.5 = 09:30). |
| `GEMINI_API_KEY` or `ESSTRACKER_GEMINI_API_KEY` | No | — | Enables AI day reports. |
| `ESSTRACKER_SMTP_HOST` | No | `127.0.0.1` | SMTP host for password reset / alerts. |
| `ESSTRACKER_SMTP_PORT` | No | `587` | SMTP port. |
| `ESSTRACKER_SMTP_USER` | No | — | SMTP username. |
| `ESSTRACKER_SMTP_PASSWORD` | No | — | SMTP password. |
| `ESSTRACKER_SMTP_FROM` | No | — | From address. |
| `ESSTRACKER_SMTP_FROM_NAME` | No | `esstracker` | From display name. |
| `ESSTRACKER_SMTP_MODE` | No | `starttls` | `starttls`, `ssl`, or `plain`. |
| `ESSTRACKER_RELEASES_DIR` | No | under data dir | Optional folder of client installers for the download page. |

### Minimal production example (HTTPS reverse proxy)

```bash
docker run -d \
  --name esstracker \
  --restart unless-stopped \
  -p 127.0.0.1:8080:8080 \
  -v /var/lib/esstracker:/data \
  -e ESSTRACKER_PUBLIC_URL=https://tracker.example.com \
  -e TIMETRACK_SERVER_DATA=/data \
  -e TIMETRACK_SECURE_COOKIES=1 \
  -e PORT=8080 \
  euclidee/esstracker-server:latest
```

Put nginx/Caddy/Traefik in front for TLS; proxy to `127.0.0.1:8080`.

---

## 4. First-time setup screen

On first boot with empty `/data`:

- **Company / brand:** name, website, email, timezone, optional logo  
- **Admin:** name, username, email, password  

After that, `/setup` is locked; only `/login` works. To redo setup, remove the data volume.

---

## 5. Persist data (important)

Always mount a volume on `/data`:

| Path in container | Contents |
|---|---|
| `/data/timetrack-server.db` | SQLite database |
| `/data/screenshots/` | Captured screenshots |
| `/data/brand/` | Uploaded company logo |
| `/data/secret_key` | Auto-generated Flask secret |

Without a volume, all data is lost when the container is removed.

---

## 6. Desktop agents

1. Create employees in the web UI (or use the admin account for a test).  
2. Install the agent (`.deb` / Windows).  
3. Sign in with username/password; set server URL to your `ESSTRACKER_PUBLIC_URL` (LAN IP or domain).

---

## 7. Build from source (optional)

```bash
git clone https://github.com/networkadminn/networkadminn.git
cd networkadminn
docker compose up --build
```

Local compose uses `./data/docker-tracker` as the data volume.

---

## 8. Publish (maintainers)

```bash
docker login -u euclidee
docker build -t euclidee/esstracker-server:0.3.19 -t euclidee/esstracker-server:latest .
docker push euclidee/esstracker-server:0.3.19
docker push euclidee/esstracker-server:latest
```

CI workflow: `.github/workflows/docker-publish.yml`  
Secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`
