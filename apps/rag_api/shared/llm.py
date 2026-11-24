"""답변 LLM 클라이언트(qa 계획 3.5). OpenAI 호환 `/v1` 비동기 채팅, Pydantic 구조화 출력(`chat.completions.parse`).

재시도하지 않습니다. 실패는 모두 `LLMFailed` 하나로 바꿔 올리고 `code` 가 약속서 03B 3.5 의 reason_code 입니다.
시간 초과만 `LLM_TIMEOUT`, 슬롯 대기 초과는 `LLM_SATURATED`, 그 밖(연결 실패·4xx·5xx·출력 길이 초과·구조화 출력
파싱 실패·모양이 깨진 200 등 모든 예외)은 `LLM_UNAVAILABLE`. TimeoutError 는 OSError 하위라 그대로 올리면 main 이
DB 장애(503)로, 그 밖의 예외는 500 으로 분류하므로 여기서 바꿈. 취소(CancelledError)는 그대로 올림.
`LLMFailed` 의 글에는 원인 예외 이름만 담음. 예외 글(pydantic 오류 전문 등)에 LLM 출력이 들어 있어 로그에 남기지 않음.
"""
import asyncio
from typing import TypeVar

import openai
import pydantic
from openai import AsyncOpenAI

from rag_api.shared.config import Config

T = TypeVar("T", bound=pydantic.BaseModel)


class LLMFailed(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = AsyncOpenAI(base_url=cfg.llm_base_url, api_key=cfg.llm_api_key, timeout=cfg.llm_timeout_s,
                                   max_retries=0)
        # ponytail: 프로세스 단위 슬롯. uvicorn 워커를 늘리면 동시 수가 워커 수만큼 곱해짐 — 그때 상한을 나누거나 공용 슬롯으로
        self._slot = asyncio.Semaphore(cfg.llm_max_concurrency)

    async def parse(self, messages: list[dict], schema: type[T]) -> T:
        cfg = self.cfg
        try:
            async with asyncio.timeout(cfg.llm_slot_wait_s):
                await self._slot.acquire()
        except TimeoutError:
            raise LLMFailed("LLM_SATURATED", f"슬롯 대기 {cfg.llm_slot_wait_s}초 초과") from None
        try:
            async with asyncio.timeout(cfg.llm_timeout_s):  # 전체 시간 상한(httpx 시간 제한은 단계마다라)
                r = await self._client.chat.completions.parse(
                    model=cfg.llm_model, messages=messages, response_format=schema,  # type: ignore[arg-type]
                    temperature=cfg.llm_temperature, max_tokens=cfg.llm_max_tokens,
                    reasoning_effort=cfg.llm_reasoning_effort or openai.omit)  # type: ignore[arg-type]  빈 값이면 안 보냄
            parsed = r.choices[0].message.parsed
        except (TimeoutError, openai.APITimeoutError) as e:
            raise LLMFailed("LLM_TIMEOUT", type(e).__name__) from e
        except Exception as e:  # 4xx·5xx·연결·길이 초과·파싱 실패, 빈 choices·{} 본문·JSON 아닌 200·전송 중 예외
            raise LLMFailed("LLM_UNAVAILABLE", type(e).__name__) from e
        finally:
            self._slot.release()
        if parsed is None:  # 거절(refusal) 등으로 구조화 출력이 없음
            raise LLMFailed("LLM_UNAVAILABLE", "구조화 출력 없음")
        return parsed

    async def close(self) -> None:
        await self._client.close()
