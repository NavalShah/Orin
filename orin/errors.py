"""Errors that cross layer boundaries. The server maps them to the API error shape."""
from __future__ import annotations

from typing import Literal, Optional

from .ir.models import Model


class OrinError(Exception):
    """An expected failure with a message a user can act on."""

    code = "orin_error"
    retryable = False

    def __init__(self, message: str, *, code: Optional[str] = None, hint: Optional[str] = None,
                 retryable: Optional[bool] = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class IngestError(OrinError):
    """Input rejected: unsupported_type, too_large, too_long, decode_failed, empty."""

    code = "ingest_error"


class UnknownFormat(OrinError):
    code = "unknown_format"


class RendererUnavailable(OrinError):
    """A notation kind this format (or this install) cannot produce (spec 11.3)."""

    code = "renderer_unavailable"

    def __init__(self, kind: str, message: str, *, alternative: Optional[str] = None):
        super().__init__(message, hint=f"Closest alternative: {alternative}." if alternative else None)
        self.kind = kind
        self.alternative = alternative


class ParseProblem(Model):
    """One problem in typed notes, located by line and column (both 1-based; spec 7.14)."""

    code: str  # E001..E008, W101 for sargam; parser-specific otherwise
    message: str  # plain language
    line: int
    column: int
    length: int = 1
    severity: Literal["error", "warning"] = "error"


class NotesParseError(OrinError):
    code = "notes_parse_error"

    def __init__(self, problems: list[ParseProblem]):
        first = next((p for p in problems if p.severity == "error"), problems[0] if problems else None)
        where = f"Line {first.line}, column {first.column}: {first.message}" if first else "The notes could not be read."
        super().__init__(where, hint="Fix the marked position and try again.")
        self.problems = problems
