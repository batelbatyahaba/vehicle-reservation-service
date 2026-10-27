import logging
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import DomainError
from app.schemas.common import ErrorResponse

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Vehicle Reservations")
app.include_router(api_router)


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(code=code, message=message).model_dump(),
    )


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, str(exc))


@app.exception_handler(RequestValidationError)
def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    message = "; ".join(_describe_validation_error(error) for error in exc.errors())
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", message
    )


def _describe_validation_error(error: dict) -> str:
    source, *path = error["loc"]
    if error["type"] == "json_invalid" or not path:
        return f"{source}: {error['msg']}"
    return f"{'.'.join(str(part) for part in path)}: {error['msg']}"


@app.exception_handler(HTTPException)
def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    phrase = HTTPStatus(exc.status_code).phrase
    return error_response(
        exc.status_code, phrase.lower().replace(" ", "_"), str(exc.detail)
    )


@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeoutError)
def handle_database_unavailable(
    request: Request, exc: OperationalError | PoolTimeoutError
) -> JSONResponse:
    logger.error("database_unavailable path=%s", request.url.path, exc_info=exc)
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "database_unavailable",
        "Database is unavailable, please retry",
    )


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled_error path=%s", request.url.path, exc_info=exc)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
    )
