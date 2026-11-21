# ContractLens AI — Instructions

## 1. Requirements

| Tool | Version | Needed for |
| --- | --- | --- |
| Python | 3.12+ | Backend |
| Node.js | 20+ (22 recommended) | Frontend |
| Docker Desktop | recent | Local PostgreSQL (or provide your own PostgreSQL 14+) |
| Tesseract OCR | optional | Reading scanned PDFs (included in the Docker image) |

## 2. Local development

### One-click launchers

- **Windows:** `Start App.bat`
- **macOS:** `Start App (Mac).command` (first run: right-click → Open, because the file is unsigned)

They are safe to run repeatedly: they never delete files or reset the database. `backend/.env` is created once with
newly generated secrets and is never overwritten.

### Manual setup

```bash
docker compose up -d db
```

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt        # macOS/Linux: .venv/bin/pip
```

Create `backend/.env` from `.env.example` and generate secrets:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the first value in `JWT_SECRET_KEY` and the second in `ENCRYPTION_KEY`. Then:

```bash
cd backend && .venv/Scripts/python -m alembic upgrade head
```

```bash
cd backend && .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```bash
cd frontend && npm ci && npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to port 8000, so cookies stay first-party.
API documentation: http://127.0.0.1:8000/api/docs (disable in production with `DOCS_ENABLED=false`).

The local database listens on `127.0.0.1:5437` (user/password `contractlens`, database `contractlens`) to avoid clashing
with other local PostgreSQL instances. Use `127.0.0.1`, not `localhost`, in `DATABASE_URL` on Windows.

### Password reset without email

If no SMTP server is configured and `ENVIRONMENT=development`, the reset link is printed to the API console. In
production nothing is printed; configure `SMTP_*` to enable resets and invitation emails. Invitations always return a
one-time link to the inviting administrator, so they work without email.

## 3. Configuration reference

All variables are documented in [.env.example](.env.example). Production-specific rules (startup fails otherwise):

- `JWT_SECRET_KEY` must be random and at least 32 characters.
- `ENCRYPTION_KEY` must be set (any random secret; a Fernet key is recommended).
- `STORAGE_BACKEND=local` is refused unless `LOCAL_STORAGE_PERSISTENT=true` (container disks are usually ephemeral).
- SQLite is refused.

### AI providers

| Provider | Variables | Default model |
| --- | --- | --- |
| OpenAI | `AI_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` | `gpt-4.1-mini` |
| Anthropic | `AI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | `claude-sonnet-5-5` |
| Google Gemini | `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL` | `gemini-2.5-flash` |

Workspace owners can also set a provider, model and their own API key in **Settings → Workspace** (stored encrypted
with `ENCRYPTION_KEY`). Without any provider, analysis is rule-based and the assistant returns relevant passages.
Token use is capped with `AI_MAX_INPUT_CHARS` and `AI_MAX_OUTPUT_TOKENS`; calls time out after `AI_TIMEOUT_SECONDS`.
When AI is enabled, contract text is sent to the chosen provider — check its data-processing terms first.

### Storage

| Backend | When to use | Notes |
| --- | --- | --- |
| `database` | Render / small–medium deployments | Bytes stored in PostgreSQL (`stored_blobs`), private and backed up with the DB |
| `s3` | Larger volumes | Any S3-compatible private bucket: AWS S3, Cloudflare R2, Backblaze B2, MinIO. Set `OBJECT_STORAGE_*`. Keep the bucket private (block public access) |
| `local` | Development, or a mounted persistent disk | Set `LOCAL_STORAGE_PERSISTENT=true` in production only when the path is a persistent volume |

Files are always downloaded through the API with signed links that expire after `DOWNLOAD_LINK_SECONDS` (default 120 s)
and re-check workspace membership; no backend ever creates public URLs.

### Background worker

The worker processes analysis jobs, reminders, retention deletion and housekeeping. By default it runs as a thread in
the API process (`RUN_WORKER_IN_API=true`). For heavier workloads run `python -m app.worker` as a separate process or
service and set `RUN_WORKER_IN_API=false` on the API. Several workers can run concurrently.

## 4. Testing and quality checks

```bash
cd backend && .venv/Scripts/python -m pytest
```

```bash
cd backend && TEST_DATABASE_URL=postgresql+psycopg://contractlens:contractlens@127.0.0.1:5437/contractlens_test .venv/Scripts/python -m pytest
```

(create the `contractlens_test` database first: `docker compose exec db createdb -U contractlens contractlens_test`)

```bash
cd backend && .venv/Scripts/python -m ruff check . && .venv/Scripts/python -m mypy app && .venv/Scripts/python -m bandit -q -c pyproject.toml -r app && .venv/Scripts/python -m pip_audit -r requirements.txt
```

```bash
cd frontend && npm run lint && npm run typecheck && npm test && npm run build && npm audit --omit=dev
```

End-to-end smoke test against any running instance (synthetic documents only, cleans up after itself):

```bash
backend/.venv/Scripts/python scripts/smoke_test.py http://localhost:8000
```

## Deployment on Render

The Blueprint in [render.yaml](render.yaml) creates:

| Resource | Type | Purpose |
| --- | --- | --- |
| `contractlens-ai` | Web service (Docker) | API + React app (same origin) + embedded worker |
| `contractlens-db` | PostgreSQL | Data, job queue and private document storage |

### Steps

1. Push the repository to GitHub.
2. In the Render Dashboard choose **New → Blueprint**, connect the repository and select `render.yaml`.
3. Review the plan for each resource (see costs below) and click **Apply**. `JWT_SECRET_KEY` and `ENCRYPTION_KEY` are
   generated automatically.
4. Optional: in the service's **Environment** tab add `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` and set
   `AI_PROVIDER` accordingly; add `SMTP_*` for email. Save to redeploy.
5. Wait for the deploy, then open `https://<service>.onrender.com/health/ready` — it reports database, migrations and
   storage status.
6. Run the smoke test: `python scripts/smoke_test.py https://<service>.onrender.com`.

Migrations run automatically (`alembic upgrade head`) before the server starts on every deploy. `FRONTEND_URL` and
`BACKEND_URL` default to Render's `RENDER_EXTERNAL_URL`; set them explicitly when you add a custom domain.

### Plans and costs (check Render's current pricing)

- **Free web service:** sleeps after ~15 minutes of inactivity; the first request afterwards takes about a minute. Queued
  jobs are stored in PostgreSQL and resume when the instance wakes.
- **Free PostgreSQL:** 1 GB, one per workspace, and **expires 30 days after creation** unless upgraded. Use a paid plan
  (for example `basic-256mb`) for data you want to keep; paid plans include backups.
- **Dedicated worker:** background workers require a paid instance; uncomment the worker in `render.yaml` and set
  `RUN_WORKER_IN_API=false`.
- For large document volumes switch to S3-compatible storage (`STORAGE_BACKEND=s3`).

### Operations

- Health checks: `/health` (liveness), `/health/ready` (database, migrations, storage).
- Logs: JSON lines in production with request IDs; request bodies and document text are never logged.
- Backups: use Render PostgreSQL backups (paid plans) — they include documents when `STORAGE_BACKEND=database`.
- Scaling: the in-process rate limiter is per instance; add an edge rate limiter when running several instances.

## 5. Project layout

```
backend/   FastAPI app (app/), Alembic migrations, tests (synthetic fixtures)
frontend/  React + TypeScript SPA
scripts/   smoke_test.py (end-to-end verification)
docs/      screenshots
Dockerfile, docker-compose.yml, render.yaml, .github/workflows/ci.yml
```
