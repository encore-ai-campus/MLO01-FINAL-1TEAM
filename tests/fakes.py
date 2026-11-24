"""단위·통합 시험이 같이 쓰는 가짜(pytest.ini 의 pythonpath 에 tests 가 있어 `from fakes import …`)."""
from rag_api.shared.llm import LLMFailed


class FakeLLM:
    """답변 LLM 대신(실제 LLM 서버에 닿지 않게). 받은 messages 를 calls 에 남김.
    `reply(근거 수)` → LLM 출력 dict(없으면 한 문장이 근거 모두를 인용), `error` 코드면 LLMFailed.
    `cfg` 는 앱이 `LLMClient(cfg)` 처럼 만들 때 받는 자리(쓰지 않음)."""
    def __init__(self, reply=None, error: str | None = None, cfg=None):
        self.reply, self.error, self.calls = reply, error, []

    async def parse(self, messages, schema):
        self.calls.append(messages)
        if self.error:
            raise LLMFailed(self.error, "가짜")
        n = messages[1]["content"].count('<evidence id="E')
        return schema.model_validate(self.reply(n) if self.reply else
                                     {"answerable": True, "sentences": [{"text": "답.", "citations": list(range(1, n + 1))}]})

    async def close(self):
        pass
