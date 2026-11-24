"""단위 시험 공용 도우미: 앱을 lifespan 없이 ASGI 로 부름(ASGITransport 는 lifespan 을 돌리지 않아 상태는 시험이
app.state 에 직접 둠). conftest 가 아닌 것은 `from conftest import` 가 integration/conftest 와 이름이 겹쳐서."""
import asyncio

import httpx


def request(app, method: str, path: str, **kw) -> httpx.Response:
    async def go():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.request(method, path, **kw)
    return asyncio.run(go())


def post(app, path: str, body, headers=None) -> httpx.Response:
    return request(app, "POST", path, json=body, headers=headers or {})
