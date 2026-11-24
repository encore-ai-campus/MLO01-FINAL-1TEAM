# 시험

- `tests/unit/`: DB 없이 도는 시험입니다. 가짜 임베더·가짜 LLM 을 쓰므로 실제 서버에 붙지 않습니다.
- `tests/integration/`: PostgreSQL 이 필요한 시험입니다. DB 환경 변수가 없으면 건너뜁니다.
- `tests/fakes.py`: 시험이 같이 쓰는 가짜(`FakeLLM`).
- `tests/fixtures/`: 통합 시험이 넣는 준비 데이터.

## 실행

```bash
pytest                      # DB 없이: unit 만 돌고 integration 은 건너뜁니다
```

## DB 시험 준비

- PostgreSQL 17, `vector` 0.8 이상, `pg_textsearch` 1.5.1 이상(`shared_preload_libraries` 에 `pg_textsearch`)
- 환경 변수 두 개를 정합니다.
  - `RAG_TEST_DATABASE_URL`: 시험용 서버 접속 주소(`postgresql://사용자@호스트:포트/postgres`). 시험이 세션마다 `rag_test_` 로 시작하는 새 DB 를 만들고 끝나면 지웁니다.
  - `RAG_INIT_SQL`: 테이블을 만드는 `init.sql` 의 경로

```bash
RAG_TEST_DATABASE_URL=... RAG_INIT_SQL=... pytest
```

시험은 설정 환경 변수를 모두 지운 뒤 필요한 것만 정하므로, 셸에 설정이 있어도 실제 임베딩·LLM 서버에 닿지 않습니다.
