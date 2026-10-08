from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
import re
from typing import TypeVar
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ValidationError

from src.metrics.external_auth import external_auth_saturation
from src.auth.external_errors import ExternalAuthError, ExternalAuthUnavailable
from src.concurrency import CapacityGateFull, CapacityGateTimedOut
from src.middleware.errors import proxy_error_response
from src.services.external_auth_runtime import ExternalAuthRuntime

T = TypeVar("T", bound=BaseModel)
BODY_LIMIT = 16384


def external_runtime(request: Request) -> ExternalAuthRuntime:
    runtime = getattr(request.app.state, "external_auth_runtime", None)
    if not isinstance(runtime, ExternalAuthRuntime):
        raise ExternalAuthUnavailable()
    return runtime


def correlation_id(request: Request) -> str:
    return request.state.external_auth_correlation_id


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate request field")
        result[key] = value
    return result


def external_request_schema(model: type[BaseModel]) -> dict[str, object]:
    """Document the strict body without letting FastAPI read it before admission."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


async def read_external_body(request: Request, model: type[T]) -> T:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise ExternalAuthError("invalid_external_request", status_code=400)
    size = request.headers.get("content-length")
    if size is not None:
        if not size.isascii() or not size.isdigit():
            raise ExternalAuthError("invalid_external_request", status_code=400)
        if len(size) > 8 or int(size) > BODY_LIMIT:
            raise ExternalAuthError("external_request_too_large", status_code=413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > BODY_LIMIT:
            raise ExternalAuthError("external_request_too_large", status_code=413)
        body.extend(chunk)
    try:
        parsed = json.loads(body, object_pairs_hook=_unique_fields)
        return model.model_validate(parsed)
    except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError) as exc:
        raise ExternalAuthError("invalid_external_request", status_code=400) from exc


class ExternalAuthRoute(APIRoute):
    """Acquire bounded ingress capacity before FastAPI reads request data."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def bounded(request: Request) -> Response:
            supplied = request.headers.get("x-correlation-id", "")
            request.state.external_auth_correlation_id = (
                supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", supplied) else uuid4().hex
            )
            runtime: ExternalAuthRuntime | None = None
            acquired = False
            try:
                runtime = external_runtime(request)
                if request.client is not None:
                    try:
                        request.state.external_auth_client = runtime.client_resolver.resolve(
                            request.client.host, request.headers.get("x-forwarded-for")
                        )
                    except ValueError as exc:
                        raise ExternalAuthError(
                            "invalid_external_request", status_code=400
                        ) from exc
                await runtime.ingress.acquire(timeout_seconds=0.001)
                acquired = True
                operation = (
                    "admin"
                    if "/ui/api/" in request.url.path
                    else request.url.path.rsplit("/", 1)[-1].replace("-", "_")
                )
                async with runtime.operation(operation):
                    response = await handler(request)
            except (CapacityGateFull, CapacityGateTimedOut):
                external_auth_saturation.labels("ingress", "full").inc()
                response = proxy_error_response(ExternalAuthUnavailable())
            except RequestValidationError:
                response = proxy_error_response(
                    ExternalAuthError("invalid_external_request", status_code=400)
                )
            except ExternalAuthError as exc:
                response = proxy_error_response(exc)
            except HTTPException as exc:
                response = JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
                )
            finally:
                if acquired and runtime is not None:
                    await runtime.ingress.release()
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["X-Correlation-ID"] = correlation_id(request)
            if response.status_code in (429, 503):
                response.headers["Retry-After"] = "60" if response.status_code == 429 else "1"
            return response

        return bounded


def require_backend_assertion_request(request: Request) -> None:
    if request.headers.get("origin") is not None or request.headers.get("cookie") is not None:
        raise ExternalAuthError()
