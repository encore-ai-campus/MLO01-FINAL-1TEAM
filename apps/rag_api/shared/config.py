"""환경 변수 설정. 앱을 띄울 때 `Config.from_env()` 로 한 번 읽습니다.

환경 변수 이름은 필드 이름의 대문자입니다(예: `KEYWORD_TOP_K`). 초기값은 knowledge_engine 구현 계획 6절,
앱 값은 검색 API 계획 3.3·6절. 숫자·스위치 값은 여기, 메인기획 고정 문구·판별 패턴은 `rules/rules.yaml`(rules.md).
"""
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import get_type_hints


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)  # 비밀번호가 들 수 있어 로그에 안 나오게
    embed_base_url: str = "http://localhost:11434/v1"
    embed_model: str = "nlpai-lab/KURE-v3"
    embed_api_key: str = field(default="unused", repr=False)
    embed_timeout_s: float = 0.5
    vector_channel_enabled: bool = True
    ontology_expansion_enabled: bool = False
    ontology_expansion_max_tokens: int = 20
    keyword_top_k: int = 20
    vector_top_k: int = 20
    rrf_k: int = 60
    rrf_weight_keyword: float = 1.0
    rrf_weight_vector: float = 1.0
    rrf_weight_graph: float = 1.0
    graph_seed_docs: int = 3  # 그래프 채널이 앞뒤 단계를 볼 상위 문서 수
    result_top_n: int = 20
    proximity_boost_enabled: bool = False
    rerank_enabled: bool = False
    discover_hint_limit: int = 3
    term_stats_refresh_s: float = 300.0
    # 메인기획 규칙 값(rules.md). 값이 바뀌면 설정만 바꿈. 판정은 쓰는 모듈의 코드가 함
    # 근거 부족(R3, A46): 상위 N 청크의 질의 토큰 덮음 최댓값이나 벡터 1위 코사인이 기준보다 작으면 근거 부족(qa)
    insufficient_coverage_min: float = 0.40
    insufficient_cosine_min: float = 0.21
    insufficient_coverage_chunks: int = 10
    # 검색 응답(약속서 03A, 검색 API 계획 6절)
    snippet_chars: int = 200
    snippets_per_doc: int = 2
    reference_templates_max: int = 5
    # 낡은 문서 배지 `unverified`(경계 계약 4.11): 기준 시각 + 점검 주기(달력 개월) < 오늘. 정관·기본 거버넌스는 긴 주기(chapter_id 가 아래 장이고 official_policy)
    verify_interval_months: int = 6
    verify_long_interval_months: int = 12
    verify_long_interval_chapters: str = "ch01"  # 쉼표로 여럿
    # AI 답변(qa 계획 3.7)
    qa_evidence_chunks: int = 5  # 근거 후보 = retrieve 상위 청크 수. R2 "상위 근거 5개"와 같은 수
    # 답변 LLM(OpenAI 호환, qa 계획 3.5·3.7). 주소가 비면 LLM 없이 대체 응답(시험이 실수로 실제 서버에 닿지 않게)
    llm_base_url: str = ""
    llm_model: str = ""  # 주소가 있으면 필수
    llm_api_key: str = field(default="unused", repr=False)
    llm_timeout_s: float = 30.0  # 개발 Mac 실측으로 맞춤. 운영 값은 분석 과제 2
    llm_max_tokens: int = 600
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = "none"  # 생각 모드 끄기. 빈 값이면 보내지 않음
    llm_max_concurrency: int = 1  # 프로세스당 동시 LLM 호출 수. LLM 서버가 실제로 동시에 처리하는 수에 맞춤
    llm_slot_wait_s: float = 2.0  # 자리가 안 나면 LLM_SATURATED 대체 응답. 백엔드 임시값(15-03)
    # 앱(main.py)만 씀. 기본값이 없는 값이라 앱이 시작할 때 확인하고, 평가·적재 스크립트는 읽지 않음
    active_packs: str = ""  # 예: base,it_service (settings.md §4)
    # 앱만 씀(기본값 있음)
    db_connect_timeout_s: float = 5.0  # 새 DB 연결 시간 제한. 넘으면 503(약속서 00 4.3)
    db_pool_size: int = 10  # 프로세스당 DB 연결 수(min=max, asyncpg 기본과 같음). 워커 수 × 이 값 ≤ max_connections 로 설치마다 맞춤
    embed_warmup_timeout_s: float = 3.0  # 시작 때 임베딩 미리 부르기 상한. 모델 올리기 약 1초, 시작을 오래 막지 않게
    rules_path: str = ""  # 메인기획 문구·패턴 파일. 빈 값이면 패키지 안 rules/rules.yaml

    def __post_init__(self):
        # 정책 숫자 범위. NaN·inf 도 막음(0 <= NaN 은 거짓)
        for name in ("insufficient_coverage_min", "insufficient_cosine_min"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name.upper()}: 0~1 이어야 합니다 ({getattr(self, name)})")
        if not 0 < self.term_stats_refresh_s < math.inf:  # 0·음수면 통계 SQL 이 쉬지 않고 돌고, nan·inf 면 갱신이 안 됨
            raise ValueError(f"TERM_STATS_REFRESH_S: 0 보다 큰 유한한 값이어야 합니다 ({self.term_stats_refresh_s})")
        for name in ("verify_interval_months", "verify_long_interval_months"):  # 0 이하면 모든 문서가 늘 낡음으로 나가거나 날짜가 거꾸로 감
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()}: 1 이상이어야 합니다 ({getattr(self, name)})")
        if self.llm_base_url and not self.llm_model:
            raise ValueError("LLM_MODEL: LLM_BASE_URL 이 있으면 모델 이름이 있어야 합니다")
        if not 0 < self.llm_timeout_s < math.inf:  # nan 이면 시간 상한이 걸리지 않음
            raise ValueError(f"LLM_TIMEOUT_S: 0 보다 큰 유한한 값이어야 합니다 ({self.llm_timeout_s})")
        for name in ("llm_slot_wait_s", "llm_temperature"):  # 슬롯 대기 0 = 기다리지 않음. temperature nan 이면 요청마다 실패
            if not 0 <= getattr(self, name) < math.inf:
                raise ValueError(f"{name.upper()}: 0 이상 유한한 값이어야 합니다 ({getattr(self, name)})")
        for name in ("llm_max_tokens", "llm_max_concurrency"):  # 동시 0 이면 모든 답변이 슬롯 초과
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()}: 1 이상이어야 합니다 ({getattr(self, name)})")
        # 크기 값·모델이 아직 없음(설계 §4.4). 켜면 시작하지 않음
        if self.proximity_boost_enabled:
            raise ValueError("PROXIMITY_BOOST_ENABLED: 근접성 보정 크기 값이 없어 켤 수 없습니다")
        if self.rerank_enabled:
            raise ValueError("RERANK_ENABLED: 재순위 모델이 없어 켤 수 없습니다")

    @property
    def long_interval_chapters(self) -> tuple[str, ...]:
        return tuple(c.strip() for c in self.verify_long_interval_chapters.split(",") if c.strip())

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        kw = {}
        types = get_type_hints(cls)  # f.type 는 문자열일 수도 있어 실제 형으로 받음
        for f in fields(cls):
            raw = env.get(f.name.upper())
            if raw is not None:
                kw[f.name] = _parse(f.name.upper(), raw, types[f.name])
        if not kw.get("database_url"):  # 빈 값도 없음으로 봄: 비면 asyncpg 가 로컬 기본 PostgreSQL 에 붙으려 함
            raise ValueError("DATABASE_URL 이 없습니다")
        return cls(**kw)


def _parse(name: str, raw: str, typ: type):
    if typ is bool:
        if raw.lower() not in ("true", "false"):
            raise ValueError(f"{name}: true 또는 false 여야 합니다 ({raw!r})")
        return raw.lower() == "true"
    return typ(raw)
