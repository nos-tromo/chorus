"""Global error handlers: generic client-visible bodies, detail to logs.

Validation errors reach the log without the values the client submitted.
An operation that needs a stated purpose and got none is a 428; one whose
audit event could not be written is a generic 500 that returns no data.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

from chorus.audit.trail import AuditFailure, PurposeRequired
from chorus.utils.audit_runtime import PURPOSE_REQUIRED_STATUS
from chorus.utils.log_redaction import redact_validation_errors


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers that keep exception detail out of response bodies.

    Args:
        app: The FastAPI application to attach the handlers to.
    """

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning(
            "Validation error on {} {}: {}",
            request.method,
            request.url.path,
            redact_validation_errors(exc.errors()),
        )
        return JSONResponse(status_code=422, content={"detail": "Invalid request."})

    @app.exception_handler(PurposeRequired)
    async def _handle_purpose_required(request: Request, exc: PurposeRequired) -> JSONResponse:
        logger.info("refused {} {}: a stated purpose is required", request.method, request.url.path)
        return JSONResponse(
            status_code=PURPOSE_REQUIRED_STATUS,
            content={"detail": "A stated purpose is required for this request."},
        )

    @app.exception_handler(AuditFailure)
    async def _handle_audit_failure(request: Request, exc: AuditFailure) -> JSONResponse:
        logger.opt(exception=exc).error(f"Audit event could not be written on {request.method} {request.url.path}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.opt(exception=exc).error(f"Unhandled error on {request.method} {request.url.path}")
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})
