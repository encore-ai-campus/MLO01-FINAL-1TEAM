import pytest

from rag_api.shared.config import Config


def test_defaults_and_env():
    c = Config.from_env({"DATABASE_URL": "postgresql://x", "KEYWORD_TOP_K": "30", "EMBED_TIMEOUT_S": "1.5",
                         "VECTOR_CHANNEL_ENABLED": "False"})
    assert (c.keyword_top_k, c.embed_timeout_s, c.vector_channel_enabled) == (30, 1.5, False)
    assert (c.vector_top_k, c.rrf_k, c.result_top_n, c.ontology_expansion_enabled) == (20, 60, 20, False)
    assert "postgresql://x" not in repr(c) and "unused" not in repr(c)  # 비밀 값은 repr 에 안 나옴


def test_llm_values_default_and_env():
    c = Config(database_url="x")
    assert (c.llm_base_url, c.llm_model, c.llm_api_key, c.llm_timeout_s, c.llm_max_tokens, c.llm_temperature,
            c.llm_reasoning_effort, c.llm_max_concurrency, c.llm_slot_wait_s, c.qa_evidence_chunks) == (
        "", "", "unused", 30.0, 600, 0.0, "none", 1, 2.0, 5)  # qa 계획 3.7. 주소가 비면 LLM 을 부르지 않음
    c = Config.from_env({"DATABASE_URL": "x", "LLM_BASE_URL": "http://h/v1", "LLM_MODEL": "m", "LLM_API_KEY": "k-비밀",
                         "LLM_TIMEOUT_S": "5", "LLM_MAX_TOKENS": "100", "LLM_TEMPERATURE": "0.2",
                         "LLM_REASONING_EFFORT": "", "LLM_MAX_CONCURRENCY": "4", "LLM_SLOT_WAIT_S": "0.5"})
    assert (c.llm_base_url, c.llm_model, c.llm_timeout_s, c.llm_max_tokens, c.llm_temperature, c.llm_reasoning_effort,
            c.llm_max_concurrency, c.llm_slot_wait_s) == ("http://h/v1", "m", 5.0, 100, 0.2, "", 4, 0.5)
    assert "k-비밀" not in repr(c)
    c = Config.from_env({"DATABASE_URL": "x", "LLM_SLOT_WAIT_S": "0", "LLM_TEMPERATURE": "0", "LLM_MAX_TOKENS": "1"})
    assert (c.llm_slot_wait_s, c.llm_temperature, c.llm_max_tokens) == (0, 0, 1)  # 경계값은 받음


def test_rule_and_app_values_default_to_current_and_read_env():
    c = Config(database_url="x")
    assert (c.insufficient_coverage_min, c.insufficient_cosine_min, c.insufficient_coverage_chunks,
            c.db_connect_timeout_s, c.db_pool_size, c.embed_warmup_timeout_s, c.rules_path) == (
        0.40, 0.21, 10, 5.0, 10, 3.0, "")
    names = ("INSUFFICIENT_COVERAGE_MIN", "INSUFFICIENT_COSINE_MIN", "INSUFFICIENT_COVERAGE_CHUNKS",
             "DB_CONNECT_TIMEOUT_S", "DB_POOL_SIZE", "EMBED_WARMUP_TIMEOUT_S", "RULES_PATH")
    values = ("0.5", "0.3", "5", "2.5", "4", "1.5", "/x/rules.yaml")
    c = Config.from_env({"DATABASE_URL": "x", **dict(zip(names, values))})
    assert (c.insufficient_coverage_min, c.insufficient_cosine_min, c.insufficient_coverage_chunks,
            c.db_connect_timeout_s, c.db_pool_size, c.embed_warmup_timeout_s, c.rules_path) == (
        0.5, 0.3, 5, 2.5, 4, 1.5, "/x/rules.yaml")
    c = Config.from_env({"DATABASE_URL": "x", "INSUFFICIENT_COVERAGE_MIN": "0", "INSUFFICIENT_COSINE_MIN": "1"})
    assert (c.insufficient_coverage_min, c.insufficient_cosine_min) == (0, 1)  # 경계값은 받음


@pytest.mark.parametrize("env", [
    {},                                                        # DATABASE_URL 없음
    {"DATABASE_URL": ""},                                       # 빈 DATABASE_URL 도 없음(로컬 기본 DB 로 붙지 않게)
    {"DATABASE_URL": "x", "VECTOR_CHANNEL_ENABLED": "yes"},    # bool 은 true/false 만
    {"DATABASE_URL": "x", "PROXIMITY_BOOST_ENABLED": "true"},  # 크기 값 없음
    {"DATABASE_URL": "x", "RERANK_ENABLED": "true"},           # 모델 없음
    *({"DATABASE_URL": "x", name: v} for name in ("INSUFFICIENT_COVERAGE_MIN", "INSUFFICIENT_COSINE_MIN")
      for v in ("-0.01", "1.01", "nan", "inf")),               # 근거 부족 기준값은 0~1
    *({"DATABASE_URL": "x", "TERM_STATS_REFRESH_S": v} for v in ("0", "-1", "nan", "inf")),  # 갱신 주기도 0 보다 큰 유한값
    {"DATABASE_URL": "x", "LLM_BASE_URL": "http://h/v1"},                          # 주소가 있는데 모델이 없음
    {"DATABASE_URL": "x", "LLM_BASE_URL": "http://h/v1", "LLM_MODEL": "m", "LLM_MAX_CONCURRENCY": "0"},  # 슬롯 0
    *({"DATABASE_URL": "x", "LLM_TIMEOUT_S": v} for v in ("0", "-1", "nan", "inf")),   # 시간 상한은 0 보다 큰 유한값
    *({"DATABASE_URL": "x", name: v} for name in ("LLM_SLOT_WAIT_S", "LLM_TEMPERATURE")
      for v in ("-0.1", "-1", "nan", "inf")),                                         # 0 이상 유한값
    *({"DATABASE_URL": "x", name: v} for name in ("LLM_MAX_TOKENS", "LLM_MAX_CONCURRENCY", "EMBED_MAX_TOKENS") for v in ("0", "-1")),
])
def test_rejected_at_start(env):
    with pytest.raises(ValueError):
        Config.from_env(env)
