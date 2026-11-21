# ContractLens AI - single production image: FastAPI API + built React app served from the same origin.
# The same image runs the background worker with:  python -m app.worker

# --- 1. Build the frontend ------------------------------------------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Runtime -----------------------------------------------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata/ \
    SERVE_FRONTEND_DIR=/app/static \
    LOCAL_STORAGE_PATH=/data/storage \
    PORT=8000

# Tesseract enables optional OCR of scanned PDF pages (English language data).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app contractlens

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY --from=web /web/dist ./static
RUN mkdir -p /data/storage && chown -R contractlens /data

USER contractlens
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

# Migrations are idempotent and run before the server starts. Production server: uvicorn without reload.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 30 --no-access-log --workers ${WEB_CONCURRENCY:-1}"]
