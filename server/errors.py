"""The one error shape (spec 13.2): {"error": {code, message, hint, retryable}}.

Everything that leaves the server as a non-2xx response goes through ``error_response``,
and every message goes through ``redact`` first, so a provider key can never ride out
inside an exception message (spec 13.4).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("orin.server")

_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$")
_KEY_SHAPES = re.compile(
    r"AIza[0-9A-Za-z_\-]{16,}"  # Google API keys
    r"|\b(?:sk|xi|pk|rk)[-_][0-9A-Za-z_\-]{16,}"  # sk_..., sk-..., xi-...
    r"|(?i:bearer)\s+[0-9A-Za-z._~+/\-]{12,}=*"
    r"|(?i:(?:api[-_]?key|xi-api-key|token|secret|sig|authorization)\s*[=:]\s*)[\"']?[0-9A-Za-z._~+/\-]{12,}"
    r"|\b[0-9a-fA-F]{40,}\b"  # long hex: tokens, signatures
)


def redact(text: Any) -> str:
    """Remove anything that looks like a credential from a message or log line."""
    out = str(text)
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _SECRET_ENV.search(name):
            out = out.replace(value, "[redacted]")
    return _KEY_SHAPES.sub("[redacted]", out)


class ApiError(StarletteHTTPException):
    """An expected failure. Subclasses HTTPException so FastAPI passes it through untouched,
    even when it is raised while the request body is still being read."""

    def __init__(self, status_code: int, code: str, message: str, *, hint: Optional[str] = None,
                 retryable: bool = False, headers: Optional[dict[str, str]] = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable


def not_found(what: str = "That item") -> ApiError:
    """Also used for another user's id: never reveal that it exists (spec 10.2)."""
    return ApiError(404, "not_found", f"{what} was not found.", hint="It may have been deleted.")


def error_body(code: str, message: str, hint: Optional[str] = None, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "message": redact(message), "hint": redact(hint) if hint else None, "retryable": bool(retryable)}


def error_response(status: int, code: str, message: str, hint: Optional[str] = None, retryable: bool = False,
                   headers: Optional[dict[str, str]] = None) -> JSONResponse:
    return JSONResponse({"error": error_body(code, message, hint, retryable)}, status_code=status, headers=headers)


INTERNAL = ("internal_error", "Something went wrong on the server.",
            "Try again. If it keeps happening, look at the server log.")


def body_for_exception(exc: BaseException) -> dict[str, Any]:
    """ErrorBody for an exception raised inside a job or a stream. Unknown errors get a
    generic message: their text may hold paths, keys or a traceback."""
    from orin.errors import OrinError

    if isinstance(exc, ApiError):
        return error_body(exc.code, exc.message, exc.hint, exc.retryable)
    if isinstance(exc, OrinError):
        return error_body(exc.code, exc.message, exc.hint, exc.retryable)
    return error_body(INTERNAL[0], INTERNAL[1], INTERNAL[2], True)


def log_exception(where: str, exc: BaseException) -> None:
    import traceback

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log.error("%s failed:\n%s", where, redact(text))


_STATUS_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    405: "method_not_allowed", 409: "conflict", 413: "too_large", 415: "unsupported_type",
    422: "validation_error", 429: "rate_limited",
}
_STATUS_MESSAGES = {
    404: "That address does not exist on the Orin server.",
    405: "That action is not available at this address.",
}


def _field(loc: tuple) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header")]
    return ".".join(parts)


def validation_message(errors: list[dict[str, Any]]) -> str:
    """Pydantic's errors as short plain sentences."""
    lines = []
    for err in errors[:3]:
        name = _field(tuple(err.get("loc", ())))
        kind = err.get("type", "")
        ctx = err.get("ctx") or {}
        if kind == "missing":
            line = f"'{name}' is required."
        elif kind == "json_invalid":
            line = "The request body is not valid JSON."
        elif kind == "string_too_short":
            line = f"'{name}' cannot be empty." if ctx.get("min_length") == 1 else f"'{name}' is too short."
        elif kind == "string_too_long":
            line = f"'{name}' is too long (at most {ctx.get('max_length')} characters)."
        elif kind in ("literal_error", "enum"):
            line = f"'{name}' must be one of: {ctx.get('expected', 'the listed values')}."
        elif kind == "extra_forbidden":
            line = f"'{name}' is not a field Orin knows."
        else:
            msg = str(err.get("msg", "is not valid"))
            line = f"'{name}': {msg[0].lower() + msg[1:]}." if name else f"{msg}."
        lines.append(line)
    more = len(errors) - len(lines)
    return " ".join(lines) + (f" (and {more} more)" if more > 0 else "")


def install(app: FastAPI) -> None:
    from orin.errors import NotesParseError, OrinError

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc, ApiError):
            return error_response(exc.status_code, exc.code, exc.message, exc.hint, exc.retryable, exc.headers)
        code = _STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
        message = _STATUS_MESSAGES.get(exc.status_code) or (exc.detail if isinstance(exc.detail, str) else "The request failed.")
        return error_response(exc.status_code, code, message, None, exc.status_code >= 500, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "validation_error", "The request is not valid. " + validation_message(list(exc.errors())),
                              "Check the highlighted fields and try again.")

    @app.exception_handler(OrinError)
    def _orin(request: Request, exc: OrinError) -> JSONResponse:
        status = 422 if isinstance(exc, NotesParseError) else 400
        return error_response(status, exc.code, exc.message, exc.hint, exc.retryable)

    @app.exception_handler(Exception)
    def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log_exception(f"{request.method} {request.url.path}", exc)
        return error_response(500, *INTERNAL, retryable=True)
