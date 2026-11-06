# Deployment

## Option A: Docker Compose on a Linux server (recommended)

**Prerequisites:** Ubuntu 22.04/24.04, Docker Engine with the Compose plugin, a static public IP (for SIP), a DNS name for the dashboard, and optionally an NVIDIA GPU with the NVIDIA Container Toolkit.

```bash
git clone <repo> voiceagent && cd voiceagent
cp .env.example .env
#   set ENVIRONMENT=production, SECRET_KEY, ADMIN_EMAIL/ADMIN_PASSWORD, POSTGRES_PASSWORD,
#   COOKIE_SECURE=true, PUBLIC_BASE_URL=https://voice.example.org, LLM_MODEL, STT_MODEL ...
python3 scripts/download_models.py          # or: docker compose run --rm app python /app/scripts/download_models.py
docker compose up -d --build                # app + postgres + ollama
docker compose exec ollama ollama pull qwen2.5:7b-instruct   # or the model in LLM_MODEL
docker compose logs -f app                  # wait for "Models ready"
```

The app container runs `alembic upgrade head` automatically on PostgreSQL. To add telephony and automation:

```bash
docker compose --profile telephony up -d    # Asterisk (see TELEPHONY_ZAMBIA.md)
docker compose --profile automation up -d   # n8n
```

### TLS reverse proxy

The app listens on `127.0.0.1:8000`. Put Caddy (automatic HTTPS) in front:

```caddyfile
voice.example.org {
    reverse_proxy 127.0.0.1:8000
}
```

WebSockets (`/ws/*`) pass through Caddy without extra configuration. With nginx, set `proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";` and `proxy_read_timeout 3600s;`. Browsers only allow microphone access on HTTPS (or localhost).

### GPU

Uncomment the `deploy.resources` blocks for `app` and `ollama` in `docker-compose.yml`. Set `STT_DEVICE=cuda` and `STT_MODEL=large-v3-turbo`. For Kokoro on GPU, install `onnxruntime-gpu` in the image instead of `onnxruntime`; the engine picks the CUDA provider automatically.

### Backups

* PostgreSQL: `docker compose exec db pg_dump -U voiceagent voiceagent | gzip > backup-$(date +%F).sql.gz` (daily cron, copied off-site).
* The `app_data` volume holds recordings and uploads, so include it if recordings are enabled.
* `.env` holds the secrets: back it up to a password manager or secret store, never to git.

## Option B: bare metal / development (no Docker)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements-dev.txt     # + `pip install piper-tts` if you want Piper
python scripts/download_models.py
ollama pull qwen2.5:7b-instruct                  # Ollama installed natively (Metal on macOS)
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env                             # SQLite is used if DATABASE_URL is unset
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Frontend development with hot reload: `cd frontend && npm run dev` (port 5173, proxies `/api` and `/ws` to :8000).

### systemd unit (bare metal)

```ini
[Unit]
Description=BongoHive Voice Agent
After=network-online.target

[Service]
User=voiceagent
WorkingDirectory=/opt/voiceagent/backend
EnvironmentFile=/opt/voiceagent/.env
ExecStart=/opt/voiceagent/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always

[Install]
WantedBy=multi-user.target
```

## Database migrations

* SQLite (single box): tables are created automatically at start-up.
* PostgreSQL: `cd backend && alembic upgrade head` (run automatically by the Docker image).
* After changing `app/models.py`: `alembic revision --autogenerate -m "describe change"`, review the file, then commit it.

## Production checklist

- [ ] `ENVIRONMENT=production` (the app refuses to start with default secrets or `COOKIE_SECURE=false`)
- [ ] HTTPS reverse proxy; ports 8000/9092/5038 not exposed publicly
- [ ] Admin password changed at first login; individual accounts per staff member with least-privilege roles
- [ ] Sample knowledge replaced with approved content
- [ ] `MAX_CONCURRENT_CALLS` set from benchmark results
- [ ] Backups scheduled and restore tested
- [ ] Compliance checklist in COMPLIANCE.md complete before `OUTBOUND_ENABLED=true`
