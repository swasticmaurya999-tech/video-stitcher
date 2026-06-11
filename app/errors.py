"""Typed application errors + a single consistent error envelope.

Every error response is `{"error": {"code": "...", "message": "..."}}`.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """A domain error that maps to a specific HTTP status + machine code."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.http_status,
            content={"error": {"code": self.code, "message": self.message}},
        )


# --- Canonical errors (codes mirror DESIGN §6D) ---
def too_many_files(n: int, mx: int) -> AppError:
    return AppError("TOO_MANY_FILES", f"Max {mx} files per request, got {n}.", 400)


def no_files() -> AppError:
    return AppError("NO_FILES", "At least one video file is required.", 400)


def unsupported_media(name: str, allowed: set[str]) -> AppError:
    return AppError(
        "UNSUPPORTED_MEDIA_TYPE",
        f"'{name}' has an unsupported extension. Allowed: {', '.join(sorted(allowed))}.",
        415,
    )


def payload_too_large(detail: str) -> AppError:
    return AppError("PAYLOAD_TOO_LARGE", detail, 413)


def invalid_duration(lo: int, hi: int) -> AppError:
    return AppError("INVALID_DURATION", f"target_duration must be between {lo} and {hi} seconds.", 400)


def storage_unavailable() -> AppError:
    return AppError("STORAGE_UNAVAILABLE", "Server is low on storage; please retry shortly.", 503)


def job_not_found(job_id: str) -> AppError:
    return AppError("JOB_NOT_FOUND", f"No job with id '{job_id}'.", 404)


def not_ready() -> AppError:
    return AppError("NOT_READY", "The generated video is not ready yet.", 409)


def job_failed(msg: str) -> AppError:
    return AppError("JOB_FAILED", msg or "Generation failed.", 409)


def output_expired() -> AppError:
    return AppError("OUTPUT_EXPIRED", "The generated video has expired and is no longer available.", 410)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return exc.to_response()


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL", "message": "An unexpected error occurred."}},
    )
