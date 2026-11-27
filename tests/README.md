# 시험

- `tests/unit/`: DB 없이 도는 시험입니다. 가짜 임베더·가짜 LLM 을 쓰므로 실제 서버에 붙지 않습니다.
- `tests/integration/`: PostgreSQL 이 필요한 시험입니다. DB 환경 변수가 없으면 건너뜁니다.
- `tests/security/`: 소스 코드를 훑는 정적 검사입니다(DB 없이 돕니다). 문서를 읽는 SQL 의 열람 조건 자리, 생성 LLM 라이브러리 import, 계정 쓰기·감사 SQL 위치, 로컬 임시 인증 패키지 import 금지를 확인합니다.
- `tests/fakes.py`: 시험이 같이 쓰는 가짜(`FakeLLM`).
- `tests/fixtures/`: 시험이 넣는 준비 데이터. `chunking_dummy.json` 은 메인기획 더미 표본(가상)에서 다시 만든 원문과 기대 조각.
- `tests/reference/`: 시험 기준 답. `chunking_reference.py` 는 메인기획 조각 규격의 쪼개기 부분(앱은 import 하지 않음).

## 실행

```bash
pytest                      # DB 없이: unit·security 만 돌고 integration 은 건너뜁니다
```

## 토크나이저가 필요한 시험

쪼개기·토큰 세기 시험은 KURE-v3 `tokenizer.json`(revision `5b0494e8`)이 필요합니다. 모델 파일이라 저장소에 없습니다.
`EMBED_TOKENIZER_PATH` 에 경로를 주면 돌고, 없으면 건너뜁니다. CI 는 고정 판을 받아 돌립니다.

```bash
EMBED_TOKENIZER_PATH=~/Final_Project_local/models/KURE-v3/tokenizer.json pytest
```

## DB 시험 준비

- PostgreSQL 17, `vector` 0.8 이상, `pg_textsearch` 1.5.1 이상(`shared_preload_libraries` 에 `pg_textsearch`)
- 환경 변수 두 개를 정합니다.
  - `RAG_TEST_DATABASE_URL`: 시험용 서버 접속 주소(`postgresql://사용자@호스트:포트/postgres`). 시험이 세션마다 `rag_test_` 로 시작하는 새 DB 를 만들고 끝나면 지웁니다.
  - `RAG_INIT_SQL`: 테이블을 만드는 `init.sql` 의 경로(저장소의 `db/init.sql`). 시험 계정은 시험이 직접 넣습니다

```bash
RAG_TEST_DATABASE_URL=... RAG_INIT_SQL=db/init.sql pytest
```

시험은 설정 환경 변수를 모두 지운 뒤 필요한 것만 정하므로, 셸에 설정이 있어도 실제 임베딩·LLM 서버에 닿지 않습니다.
