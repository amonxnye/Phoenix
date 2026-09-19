# Phoenix — the Governor console, the Mechanic and the improvement cycle, one process.
# Standard-library HTTP server; requirements.txt is the whole dependency list.
FROM python:3.11-slim

# util-linux gives `unshare`, which the worker sandbox and the improvement oracle use
# for a private network namespace (Article V). git is optional: the mechanic reads
# history facts when a .git directory is present, and refuses honestly when it is not.
RUN apt-get update && apt-get install -y --no-install-recommends util-linux git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ARG PHOENIX_COMMIT=""
ENV PHOENIX_COMMIT=$PHOENIX_COMMIT \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8788 GOV_DATA_DIR=/data

# Every record lives under /data — mount a volume there or lose it on every restart.
VOLUME ["/data"]
EXPOSE 8788
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/healthz || exit 1

CMD ["python", "gov/sim_console.py", "--seed"]
