"""답변 LLM 클라이언트(qa 계획 3.5). 실제 AsyncOpenAI 를 쓰고 HTTP 만 httpx MockTransport 로 바꿈(서버에 닿지 않음)."""
import asyncio
import json

import httpx
import openai
import pytest
from pydantic import BaseModel

from rag_api.shared import llm
from rag_api.shared.config import Config
from rag_api.shared.llm import LLMClient, LLMFailed


class Out(BaseModel):
    answerable: bool


def completion(content: str | None = '{"answerable": true}', finish: str = "stop", refusal: str | None = None):
    return {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, "refusal": refusal}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def mock_llm(monkeypatch, handler, **cfg) -> tuple[LLMClient, list]:
    """HTTP 만 `handler` 로 바꾼 실제 LLMClient 와 받은 요청 목록. 코드가 정한 AsyncOpenAI 인자(재시도 0 등)는 그대로."""
    requests = []

    async def record(request):
        requests.append(request)
        return await handler(request)
    real = openai.AsyncOpenAI
    # openai 3 은 안에서 httpx2 를 쓰지만 httpx 클라이언트도 받아 같은 예외로 바꿈(시험은 requirements 의 httpx 로)
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kw: real(
        **kw, http_client=httpx.AsyncClient(transport=httpx.MockTransport(record))))  # type: ignore[arg-type]
    c = Config(database_url="x", llm_base_url="http://unused.invalid/v1", llm_model="m", **cfg)
    return LLMClient(c), requests


@pytest.fixture
def client(monkeypatch):
    return lambda handler, **cfg: mock_llm(monkeypatch, handler, **cfg)


async def ok(request):
    return httpx.Response(200, json=completion())


def call(c, n=1):
    async def go():
        return await asyncio.gather(*(c.parse([{"role": "user", "content": "q"}], Out) for _ in range(n)),
                                    return_exceptions=True)
    return asyncio.run(go())


def test_request_body_and_parsed(client):
    c, requests = client(ok)
    assert call(c) == [Out(answerable=True)]
    body = json.loads(requests[0].content)
    assert (body["model"], body["temperature"], body["max_tokens"], body["reasoning_effort"]) == ("m", 0.0, 600, "none")
    assert body["response_format"]["type"] == "json_schema"  # 구조화 출력
    c, requests = client(ok, llm_reasoning_effort="")
    call(c)
    assert "reasoning_effort" not in json.loads(requests[0].content)  # 빈 값이면 보내지 않음


async def status_500(request):
    return httpx.Response(500, json={"error": {"message": "boom"}})


async def context_400(request):
    return httpx.Response(400, json={"error": {"message": "exceeds the available context size"}})


async def refused(request):
    return httpx.Response(200, json=completion(content=None, refusal="거절"))


async def length(request):
    return httpx.Response(200, json=completion('{"answera', finish="length"))


SECRET = "내 연봉 9876만원"  # LLM 출력에 든 질문 조각. 예외 글·로그에 남으면 안 됨


async def bad_json(request):
    return httpx.Response(200, json=completion(f'{{"answerable": "{SECRET}"}}'))  # pydantic 오류 글에 입력값이 들어감


async def no_choices(request):
    return httpx.Response(200, json={**completion(), "choices": []})


async def empty_body(request):
    return httpx.Response(200, json={})


async def not_json(request):
    return httpx.Response(200, text="<html>점검 중</html>", headers={"content-type": "application/json"})


async def transport_boom(request):
    raise RuntimeError(f"전송 중 예상 밖 예외 {SECRET}")


async def connect_error(request):
    raise httpx.ConnectError("연결 거부")


async def read_timeout(request):
    raise httpx.ReadTimeout("읽기 시간 초과")  # AsyncOpenAI 가 APITimeoutError 로 바꿈


async def hang(request):
    await asyncio.sleep(5)


@pytest.mark.parametrize("handler, code", [
    (status_500, "LLM_UNAVAILABLE"), (context_400, "LLM_UNAVAILABLE"), (refused, "LLM_UNAVAILABLE"),
    (length, "LLM_UNAVAILABLE"), (bad_json, "LLM_UNAVAILABLE"), (connect_error, "LLM_UNAVAILABLE"),
    (no_choices, "LLM_UNAVAILABLE"), (empty_body, "LLM_UNAVAILABLE"), (not_json, "LLM_UNAVAILABLE"),
    (transport_boom, "LLM_UNAVAILABLE"), (read_timeout, "LLM_TIMEOUT"), (hang, "LLM_TIMEOUT")])
def test_errors_become_one_code_without_retry(client, handler, code):
    c, requests = client(handler, llm_timeout_s=0.1)
    (e,) = call(c)
    assert isinstance(e, LLMFailed) and e.code == code and not isinstance(e, OSError)  # main 의 503·500 분류에 걸리지 않음
    assert "9876" not in str(e)  # 글에는 원인 예외 이름만(LLM 출력 없음)
    assert len(requests) == 1  # 재시도 없음
    (again,) = call(c)  # 실패해도 슬롯을 돌려줌(동시 1 에서 다음 호출이 슬롯 초과가 아님)
    assert isinstance(again, LLMFailed) and again.code == code


def test_cancel_propagates_and_returns_slot(client):
    """요청 취소(클라이언트가 끊음 등)는 대체 응답으로 삼키지 않고 그대로 올림. 슬롯은 돌려줌."""
    c, requests = client(hang, llm_timeout_s=5)

    async def sent():
        while not requests:
            await asyncio.sleep(0.001)

    async def go():
        task = asyncio.create_task(c.parse([], Out))
        await asyncio.wait_for(sent(), 2)  # 서버에 보낸 뒤(응답 기다리는 중)에 취소. 안 나가면 멈추지 않고 실패
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return c._slot.locked()
    assert asyncio.run(go()) is False  # 동시 1 의 자리가 돌아옴


def test_slot_saturated_then_released(client):
    gate = asyncio.Event()

    async def wait_gate(request):
        await gate.wait()
        return httpx.Response(200, json=completion())

    c, requests = client(wait_gate, llm_slot_wait_s=0.05)

    async def go():
        first = asyncio.create_task(c.parse([], Out))
        await asyncio.sleep(0.01)
        with pytest.raises(LLMFailed) as e:
            await c.parse([], Out)  # 자리 1개가 찼고 0.05초 안에 안 남
        gate.set()
        return e.value.code, await first, await c.parse([], Out)
    assert asyncio.run(go()) == ("LLM_SATURATED", Out(answerable=True), Out(answerable=True))
    assert len(requests) == 2  # 슬롯 초과는 서버에 보내지 않음
