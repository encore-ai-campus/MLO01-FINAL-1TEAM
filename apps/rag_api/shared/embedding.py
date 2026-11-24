"""임베딩 클라이언트 (경계 계약 3.1~3.4).

접두어(`query: `·`passage: `)는 이 모듈에서만 붙입니다. 런타임 옵션(`input_type` 등)은 쓰지 않습니다.
"""
import math
import unicodedata

from openai import OpenAI

EMBED_DIM = 1024  # chunks.embedding HALFVEC(1024). 모델 출력 2,048 의 앞부분(MRL)


def embed_text(title: str, chapter_id: str | None, chapter_name: str | None, section_path: str, content: str) -> str:
    """문서 조각 입력(3.2). 정제 스크립트 `build_pgvector_chunks.py` 의 `embed_header` 와 같은 모양."""
    return f"[{title} | {chapter_id or 'ch00'} {chapter_name or '공통·미분류'} | {section_path}]\n{content}"


def _to_unit(vec: list[float]) -> list[float]:
    """앞 1,024 개로 자르고 길이 1 로 맞춤(3.3)."""
    if len(vec) < EMBED_DIM:
        raise ValueError(f"임베딩 차원 {len(vec)} < {EMBED_DIM}")
    if not all(map(math.isfinite, vec)):
        raise ValueError("임베딩에 NaN·inf 가 있음")
    vec = vec[:EMBED_DIM]
    norm = math.hypot(*vec)
    if norm == 0:
        raise ValueError("임베딩 벡터 길이가 0")
    return [x / norm for x in vec]


class OpenAIEmbeddingClient:
    """OpenAI 호환 `/v1/embeddings`(Ollama·vLLM). 실패·시간 초과는 예외로 올립니다(서버는 키워드로 줄임)."""

    def __init__(self, base_url: str, model: str, timeout_s: float, api_key: str = "unused", client: OpenAI | None = None):
        self._model = model
        # 재시도하지 않음: 서버는 바로 degraded 로, 파이프라인은 다음 실행에서 다시 함(3.4)
        self._client = client or OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s, max_retries=0)

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        # SDK 기본값 base64 는 런타임마다 지원이 달라 float 로 받음
        resp = self._client.embeddings.create(model=self._model, input=inputs, encoding_format="float")
        if len(resp.data) != len(inputs):
            raise ValueError(f"임베딩 {len(resp.data)}개, 입력 {len(inputs)}개")
        return [_to_unit(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]

    def embed_query(self, text: str) -> list[float]:
        """원래 질문(NFC 만). Kiwi 토큰이 아님(3.2)."""
        return self._embed(["query: " + unicodedata.normalize("NFC", text)])[0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """`texts` 는 `embed_text` 결과."""
        return self._embed(["passage: " + t for t in texts]) if texts else []
