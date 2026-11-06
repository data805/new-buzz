# ---- frontend build ----
FROM node:22.20.0-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- backend runtime ----
FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY sample_data/ sample_data/
COPY --from=web /web/dist frontend/dist
RUN mkdir -p /app/data /app/models && chown -R app:app /app
USER app
WORKDIR /app/backend
EXPOSE 8000 9092
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD curl -fsS http://localhost:8000/api/health || exit 1
# Migrations run first on Postgres; SQLite creates tables itself.
CMD ["sh", "-c", "case \"$DATABASE_URL\" in postgresql*) alembic upgrade head ;; esac && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
