"""FastAPI application factory."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import assistant, auth, contracts, downloads, insights, system, workspaces
from app.core.config import get_settings
from app.core.logging import configure_logging

log = logging.getLogger("contractlens.api")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'none'")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    stop = threading.Event()
    thread = None
    if s.run_worker_in_api and s.environment != "test":
        from app.worker.runner import run_forever

        thread = threading.Thread(target=run_forever, args=(stop,), name="embedded-worker", daemon=True)
        thread.start()
        log.info("Embedded background worker started")
    yield
    stop.set()
    if thread:
        thread.join(timeout=10)


def create_app() -> FastAPI:
    configure_logging()
    s = get_settings()
    app = FastAPI(
        title="ContractLens AI API", version="1.0.0", lifespan=lifespan,
        docs_url="/api/docs" if s.docs_enabled else None, redoc_url=None,
        openapi_url="/api/openapi.json" if s.docs_enabled else None,
    )

    if s.allowed_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=s.allowed_origins, allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Requested-With"], max_age=600,
        )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()
        # Reject oversized bodies early (uploads get the configured limit, everything else 1 MB).
        length = request.headers.get("content-length")
        is_upload = request.method == "POST" and request.url.path.rstrip("/").endswith("/contracts")
        limit = s.max_upload_bytes + 1024 * 1024 if is_upload else 1024 * 1024
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            log.exception("Unhandled error", extra={"request_id": request_id, "path": request.url.path})
            response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Request-ID"] = request_id
        if s.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        log.info("%s %s %s", request.method, request.url.path, response.status_code,
                 extra={"request_id": request_id, "method": request.method, "path": request.url.path,
                        "status": response.status_code, "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo submitted values back (they may contain passwords or document text).
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg", "Invalid value")} for e in exc.errors()]
        return JSONResponse({"detail": "Validation error", "errors": errors}, status_code=422)

    for module in (auth, workspaces, contracts, assistant, insights, downloads, system):
        app.include_router(module.router, prefix="/api")

    # HEAD is accepted too, because many uptime monitors probe with HEAD requests.
    @app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
    def root_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.api_route("/health/ready", methods=["GET", "HEAD"], include_in_schema=False)
    def root_ready(request: Request, response: Response) -> dict[str, object]:
        from app.core.db import SessionLocal

        db = SessionLocal()
        try:
            return system.ready(db, response)
        finally:
            db.close()

    # Optionally serve the built single-page app from the same origin (one service, first-party cookies).
    dist = Path(s.serve_frontend_dir) if s.serve_frontend_dir else None
    if dist and (dist / "index.html").exists():
        index = dist / "index.html"
        root = dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Response:
            if full_path.startswith("api/"):
                raise HTTPException(404, "Not found")
            candidate = (root / full_path).resolve()
            if full_path and root in candidate.parents and candidate.is_file():
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if full_path.startswith("assets/") else {}
                return FileResponse(candidate, headers=headers)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    return app


app = create_app()
