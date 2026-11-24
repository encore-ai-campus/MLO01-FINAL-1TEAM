"""통합 시험 준비(계획 4.2). `RAG_TEST_DATABASE_URL`·`RAG_INIT_SQL` 이 없으면 건너뜁니다.

세션마다 새 DB 를 만들어 init.sql 과 BM25 DDL(계약 2.6)을 돌리고 `tests/fixtures/retrieval_docs.json` 을 넣습니다.
DB 에 `enable_seqscan`·`enable_sort` 를 끄고 `hnsw.ef_search = 1` 을 걸어, 작은 데이터에서도 BM25·HNSW 인덱스
스캔으로 돌게 합니다(켜 두면 플래너가 문서부터 거른 뒤 정렬해 상위 K 채우기를 시험하지 못함).
"""
import asyncio
import dataclasses
import json
import math
import os
import unicodedata
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
from fakes import FakeLLM

from rag_api.settings import ontology
from rag_api.shared.config import Config
from rag_api.shared.db import BM25_DDL
from rag_api.shared.embedding import EMBED_DIM, embed_text
from rag_api.shared.tokenize import tsv_source

FIXTURE = json.loads((Path(__file__).parents[1] / "fixtures" / "retrieval_docs.json").read_text(encoding="utf-8"))
DOCS, CONCEPTS = FIXTURE["documents"], FIXTURE["concepts"]

def fake_vec(text: str) -> list[float]:
    """글자로 정해지는 가짜 단위 벡터: 공백 뺀 글자 두 개씩을 1,024 칸에 셈."""
    s = "".join(unicodedata.normalize("NFC", text).lower().split())
    v = [0.0] * EMBED_DIM
    for a, b in zip(s, s[1:]):
        v[zlib.crc32((a + b).encode()) % EMBED_DIM] += 1.0
    n = math.hypot(*v) or 1.0
    return [x / n for x in v]


class FakeEmbedder:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail

    def embed_query(self, text: str) -> list[float]:
        if self.fail:
            raise self.fail
        return fake_vec(text)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [fake_vec(t) for t in texts]


def chunk_embed_text(doc: dict, section: str, content: str) -> str:
    return embed_text(doc["title"], None, None, section, content)


async def _load(conn) -> None:
    for d in DOCS:
        await conn.execute(
            "INSERT INTO documents (doc_id, title, doc_type, source_tier, knowledge_pack, dept_owner, allowed_depts,"
            " is_discoverable, ontology_graph_meta) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)",
            d["doc_id"], d["title"], d.get("doc_type", "official_policy"), d.get("source_tier", 1), d["pack"],
            d["dept_owner"], d["allowed_depts"], d.get("discoverable", False), json.dumps(d.get("graph", {})))
        for ver in d["versions"]:
            vid = await conn.fetchval(
                "INSERT INTO document_versions (doc_id, version_tag, raw_markdown, content_hash, version_status)"
                " VALUES ($1, $2, '-', $3, $4) RETURNING version_id", d["doc_id"], ver["tag"], "0" * 64, ver["status"])
            for i, content in enumerate(ver["chunks"], 1):
                section = f"제{i}조"
                await conn.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, version_id, chunk_index, section_path, content, tsv, embedding)"
                    " VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('simple', $7), $8::halfvec)",
                    f"{d['doc_id']}::{ver['tag']}::c{i:03d}", d["doc_id"], vid, i, section, content,
                    tsv_source(d["title"], section, content),
                    str(fake_vec(chunk_embed_text(d, section, content))))
            if ver.get("current"):
                await conn.execute("UPDATE documents SET current_version_id = $1 WHERE doc_id = $2", vid, d["doc_id"])


@pytest.fixture(scope="session")
def test_dsn():
    url, init_sql = os.environ.get("RAG_TEST_DATABASE_URL"), os.environ.get("RAG_INIT_SQL")
    if not url or not init_sql:
        pytest.skip("RAG_TEST_DATABASE_URL·RAG_INIT_SQL 이 없음")
    name = f"rag_test_{uuid.uuid4().hex[:8]}"
    dsn = urlsplit(url)._replace(path="/" + name).geturl()

    async def setup():
        admin = await asyncpg.connect(url)
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.execute(f'ALTER DATABASE "{name}" SET enable_seqscan = off')
        await admin.execute(f'ALTER DATABASE "{name}" SET enable_sort = off')
        await admin.execute(f'ALTER DATABASE "{name}" SET hnsw.ef_search = 1')
        # 설치의 DB 기본 시간대 설정이 빠진 경우(서버 시간대가 UTC 등): 앱이 연결마다 Asia/Seoul 을 정해 한국 시각으로 쓰는지 봄(auth 계획 4.2)
        await admin.execute(f'ALTER DATABASE "{name}" SET timezone = \'UTC\'')
        await admin.close()
        conn = await asyncpg.connect(dsn)
        await conn.execute(Path(init_sql).read_text(encoding="utf-8"))
        await conn.execute(BM25_DDL)
        for c in CONCEPTS:
            await conn.execute(
                "INSERT INTO ontology_concepts (concept_id, canonical_term, synonyms, related_doc_ids, description, knowledge_pack, dept_owner)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7)", c["concept_id"], c["canonical_term"], c["synonyms"],
                c["related_doc_ids"], c["description"], c["knowledge_pack"], c.get("dept_owner"))
        ontology._current = None
        await ontology.reload_if_changed(conn)  # init.sql 시드 + 시험 개념으로 사전을 올림
        await _load(conn)
        await conn.close()

    async def teardown():
        admin = await asyncpg.connect(url)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()

    asyncio.run(setup())
    yield dsn
    asyncio.run(teardown())


@pytest.fixture
def with_pool(test_dsn):
    """`with_pool(async fn(pool))` 를 새 이벤트 루프에서 돌림."""
    def run(fn):
        async def main():
            pool = await asyncpg.create_pool(test_dsn)
            try:
                return await fn(pool)
            finally:
                await pool.close()
        return asyncio.run(main())
    return run


@pytest.fixture
def api(test_dsn, monkeypatch):
    """`api(async fn(client, conn), embedder=None, llm=None)` 를 앱 lifespan 안에서 httpx ASGI 전송으로 돌림(검색 API 계획 4.2).
    임베딩 클라이언트는 FakeEmbedder(또는 `embedder`), 답변 LLM 클라이언트는
    FakeLLM(또는 `llm(cfg)`)으로 바꿔 실제 임베딩·LLM 서버에 닿지 않음. 설정 환경 변수는 모두 지운 뒤 필요한 것만
    정함(셸 값에 흔들리지 않게)."""
    from rag_api import main  # 쓸 때만 불러옴: 앱이 필요 없는 DB 시험(검색 엔진)은 앱 모듈 없이도 돎
    for f in dataclasses.fields(Config):
        monkeypatch.delenv(f.name.upper(), raising=False)
    for k, v in {"DATABASE_URL": test_dsn, "ACTIVE_PACKS": "base,it_service",
                 "VECTOR_CHANNEL_ENABLED": "true", "EMBED_BASE_URL": "http://unused.invalid/v1",
                 "LLM_BASE_URL": "http://unused.invalid/v1", "LLM_MODEL": "unused"}.items():
        monkeypatch.setenv(k, v)

    def run(fn, embedder=None, llm=None):
        monkeypatch.setattr(main, "OpenAIEmbeddingClient", lambda *args: embedder or FakeEmbedder())
        monkeypatch.setattr(main, "LLMClient", llm or (lambda cfg: FakeLLM(cfg=cfg)))

        async def go():
            app = main.create_app()
            async with main.lifespan(app):
                transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    conn = await asyncpg.connect(test_dsn)
                    try:
                        return await fn(client, conn)
                    finally:
                        await conn.close()
        return asyncio.run(go())
    return run
