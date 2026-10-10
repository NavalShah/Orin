"""The Orin API: a thin FastAPI layer over ``orin/`` (spec 3.3). Contract: server/schemas.py.

    ORIN_OFFLINE=1 uvicorn server.main:app --port 8000

Nothing from the pipeline, the conversion engine or the AI layer is imported here; the
services import them when a request needs them, so the app starts without them.

Security notes (spec 13.4):
  * CORS allows only ORIN_APP_ORIGIN.
  * Sessions are bearer tokens in the Authorization header, never cookies, so a
    cross-site request carries no credentials and there is no CSRF surface.
  * Provider keys are read by ``orin.ai`` only; every error message and log line written
    here passes through ``errors.redact``.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import errors
from .config import Settings
from .db import Database
from .deps import AppCtx
from .jobs import JobManager
from .ratelimit import RateLimiter
from .schemas import API_PREFIX
from .storage import LocalStorage, UrlSigner

MAX_JSON_BYTES = 8 * 1024 * 1024  # request bodies other than uploads (a project with many MIDI notes fits easily)
MULTIPART_SLACK = 64 * 1024  # boundaries and part headers around an upload


class Guard:
    """Pure ASGI middleware, inside the CORS layer so the browser can read its answers.

    1. Caps request bodies while they stream in, so an oversized upload is refused
       without being buffered or written to disk first.
    2. Turns an unhandled exception into the standard error shape. The traceback goes
       to the server log (redacted), never to the client.
    """

    def __init__(self, app: Any, upload_path: str, upload_mb: float):
        self.app = app
        self.upload_path = upload_path
        self.upload_mb = upload_mb

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_upload = scope["method"] == "POST" and scope["path"].rstrip("/") == self.upload_path
        limit = int(self.upload_mb * 1024 * 1024) + MULTIPART_SLACK if is_upload else MAX_JSON_BYTES

        def too_large() -> errors.ApiError:
            if is_upload:
                from .services.assets import too_large as upload_too_large

                return upload_too_large(self.upload_mb)
            return errors.ApiError(413, "too_large", "That request is too large.")

        started = False
        seen = 0

        async def counted_receive() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise too_large()  # an HTTPException: FastAPI passes it to the error handlers
            return message

        async def tracked_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        declared = dict(scope["headers"]).get(b"content-length", b"")
        try:
            if declared.isdigit() and int(declared) > limit:
                exc = too_large()
                response = errors.error_response(exc.status_code, exc.code, exc.message, exc.hint)
                await response(scope, receive, tracked_send)
                return
            await self.app(scope, counted_receive, tracked_send)
        except Exception as exc:
            errors.log_exception(f"{scope['method']} {scope['path']}", exc)
            if started:
                raise
            response = errors.error_response(500, *errors.INTERNAL, retryable=True)
            await response(scope, receive, send)


def build_context(settings: Settings) -> AppCtx:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "orin.db")
    return AppCtx(
        settings=settings,
        db=db,
        storage=LocalStorage(settings.data_dir / "assets"),
        signer=UrlSigner(settings.secret_key, settings.asset_url_ttl_s, API_PREFIX),
        jobs=JobManager(db, workers=settings.job_workers),
        limiter=RateLimiter(settings.rate_limits),
    )


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    if settings is None:
        from dotenv import load_dotenv

        load_dotenv()  # the only place .env is loaded; values are never printed
        settings = Settings.from_env()
    if settings.offline:
        os.environ["ORIN_OFFLINE"] = "1"  # the AI layer reads the environment itself, at call time
    ctx = build_context(settings)

    # Startup housekeeping (spec 13.3): nothing from a previous process is still running.
    from .services import assets as asset_service
    from .services import conversions as conversion_service

    ctx.jobs.recover()
    conversion_service.fail_interrupted(ctx)
    asset_service.sweep_stems(ctx)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        ctx.jobs.shutdown()

    import orin

    app = FastAPI(
        title="Orin API", version=orin.__version__, lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs", openapi_url=f"{API_PREFIX}/openapi.json", redoc_url=None,
    )
    app.state.ctx = ctx
    errors.install(app)

    from .routes import assets, auth, chat, convert, jobs, meta, projects

    for module in (meta, auth, assets, jobs, convert, chat, projects):
        app.include_router(module.router, prefix=API_PREFIX)

    app.add_middleware(Guard, upload_path=f"{API_PREFIX}/assets", upload_mb=settings.max_upload_mb)
    app.add_middleware(  # added last = outermost: every response, errors included, gets the CORS headers
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "Range"],
        expose_headers=["Content-Disposition", "Content-Range", "Accept-Ranges", "Retry-After"],
        allow_credentials=False,
        max_age=600,
    )
    return app


def __getattr__(name: str) -> Any:
    """``server.main:app`` for uvicorn, built on first use so that importing this module
    (for ``create_app``, in tests) does not open the database or read the environment."""
    if name == "app":
        app = create_app()
        globals()["app"] = app
        return app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
