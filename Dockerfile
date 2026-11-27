# ESS Tracker server — self-hosted web UI + agent API
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TIMETRACK_SERVER_DATA=/data \
    ESSTRACKER_PUBLIC_URL=http://localhost:8080 \
    PORT=8080

WORKDIR /app

RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      libjpeg62-turbo zlib1g libpng16-16 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml TIMETRACK.md ./
COPY timetrack ./timetrack

RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir . gunicorn

RUN mkdir -p /data

# Run as root in the image for simple bind-mount permissions on localhost.
# Harden with a non-root user when publishing to Docker Hub.
EXPOSE 8080
VOLUME ["/data"]

CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT:-8080} -w 2 -k gthread --threads 4 --timeout 120 timetrack.server.wsgi:app"]
