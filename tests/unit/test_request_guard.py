from types import SimpleNamespace

from starlette.responses import JSONResponse

from app.api.middleware import RequestGuard


async def test_buffered_body_is_delivered_once_and_preserves_disconnect(settings):
    received = iter(
        [
            {"type": "http.request", "body": b'{"x":', "more_body": True},
            {"type": "http.request", "body": b"1}", "more_body": False},
            {"type": "http.disconnect"},
        ]
    )
    sent = []

    async def receive():
        return next(received)

    async def send(message):
        sent.append(message)

    async def application(scope, receive, send):
        assert await receive() == {
            "type": "http.request",
            "body": b'{"x":1}',
            "more_body": False,
        }
        assert await receive() == {"type": "http.disconnect"}
        await JSONResponse({"ok": True})(scope, receive, send)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/analyze",
        "app": SimpleNamespace(state=SimpleNamespace(settings=settings)),
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {settings.agent_api_key.get_secret_value()}".encode()),
        ],
    }
    await RequestGuard(application)(scope, receive, send)
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b'{"ok":true}'
