import asyncio
from uuid import UUID, uuid4

from starlette._utils import get_route_path
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.security import authenticate
from app.core.config import Settings
from app.core.errors import ServiceError
from app.observability.logging import event


class RequestGuard:
    """Bound streamed bodies before FastAPI's JSON parser and before model execution."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings: Settings = scope["app"].state.settings
        headers = {
            name.decode("latin1").lower(): value.decode("latin1")
            for name, value in scope["headers"]
        }
        try:
            request_id = str(UUID(headers.get("x-request-id", "")))
        except ValueError:
            request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def wrapped_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message["headers"].extend(
                    [(b"x-request-id", request_id.encode()), (b"cache-control", b"no-store")]
                )
            await send(message)

        async def safe_dispatch() -> None:
            try:
                return await self.app(scope, receive, wrapped_send)
            except Exception as exc:
                # Deep/malformed JSON and framework failures must not expose tracebacks.
                code, status = (
                    ("invalid_request", 422)
                    if isinstance(exc, RecursionError)
                    else ("internal_error", 500)
                )
                event(request_id, "failed", error=code)
                if started:
                    return
                response = JSONResponse(
                    {
                        "error": {
                            "code": code,
                            "message": "Požiadavku sa nepodarilo spracovať.",
                            "request_id": request_id,
                        }
                    },
                    status_code=status,
                )
                return await response(scope, receive, wrapped_send)

        # Match the router's path handling, including deployments under a URL prefix.
        path = get_route_path(scope).rstrip("/")
        if path in {"/v1/analyze", "/v1/prepare-xlsx"} and scope["method"] == "POST":
            try:
                authenticate(
                    headers.get("authorization"), settings.agent_api_key.get_secret_value()
                )
                is_workbook = path == "/v1/prepare-xlsx"
                expected_content_type = (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    if is_workbook
                    else "application/json"
                )
                body_limit = settings.max_xlsx_bytes if is_workbook else settings.max_request_bytes
                content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
                if content_type != expected_content_type:
                    raise ServiceError("unsupported_media_type", "Nepodporovaný typ obsahu.", 415)
                if headers.get("content-encoding", "identity").strip().lower() != "identity":
                    raise ServiceError(
                        "unsupported_encoding", "Komprimovaný vstup nie je podporovaný.", 415
                    )
                body_size = 0
                chunks = []
                async with asyncio.timeout(settings.body_timeout_seconds):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        body_size += len(chunk)
                        if body_size > body_limit:
                            raise ServiceError(
                                "request_too_large", "Požiadavka prekračuje povolený limit.", 413
                            )
                        chunks.append(chunk)
                        if not message.get("more_body"):
                            break
                body = b"".join(chunks)
                original_receive = receive
                body_sent = False

                async def buffered_receive() -> Message:
                    nonlocal body_sent
                    if not body_sent:
                        body_sent = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await original_receive()

                receive = buffered_receive
            except TimeoutError:
                error = ServiceError(
                    "request_timeout", "Časový limit prijatia požiadavky bol prekročený.", 408
                )
            except ServiceError as exc:
                error = exc
            else:
                return await safe_dispatch()
            event(request_id, "rejected", error=error.code)
            response = JSONResponse(
                {"error": {"code": error.code, "message": error.message, "request_id": request_id}},
                status_code=error.status_code,
                headers={"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None,
            )
            return await response(scope, receive, wrapped_send)
        return await safe_dispatch()
