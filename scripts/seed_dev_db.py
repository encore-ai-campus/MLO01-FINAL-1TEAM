"""개발 DB 적재(knowledge_engine 구현 계획 4.4). 정제본과 init.sql 로 검색 평가용 개발 DB 를 채웁니다.

개발·평가용입니다. 파이프라인의 시드 들여오기 도구(경계 계약 5.2)를 대신하지 않습니다.
- 정제본 `documents.yaml`(버전마다 한 행)·`chunks.yaml`·`chunks_archived.yaml` 을 그대로 넣습니다.
  is_discoverable·ontology_graph_meta 도 정제본 값을 씁니다(운영에서는 관리 화면·파이프라인 컴파일이 씀).
- tsv 는 `shared.tokenize.tsv_source`(사전은 init.sql 온톨로지), 임베딩은 OpenAI 호환 런타임(Ollama 등)의 KURE-v3 입니다.
- init.sql v1.11 에 BM25 DDL 이 없어 계약 2.6 의 DDL(`shared.db.BM25_DDL`)을 더 돌립니다.
- 원문(raw_markdown)은 정제 원문(documents.yaml 의 `relative_path`, rag_ready 의 부모 폴더 기준)의 본문에 경계 계약 5.1 모양의 버전 머리말을 붙여 넣습니다
  (원문 보기·이전 버전 열람 판정이 읽음). 개념 태그(concept_ids)는 빈 배열로 둡니다.
- Ollama /v1 은 truncate 를 넘기지 않아 num_ctx 를 넘는 입력은 조용히 잘릴 수 있습니다(지금 데이터는 최대 679 토큰).

환경 변수
- DATABASE_URL: 만들 개발 DB(예: postgresql://postgres@127.0.0.1:5432/rag_dev). 같은 서버의 postgres DB 로 붙어 만듭니다
- RAG_INIT_SQL: init.sql 경로
- RAG_DATA_DIR: 정제본 rag_ready 폴더(documents.yaml 등이 있는 곳)
- EMBED_BASE_URL·EMBED_MODEL 등: shared.config

사용: PYTHONPATH=apps python scripts/seed_dev_db.py [--reset]   (--reset: 같은 이름의 DB 를 지우고 다시 만듦)
"""
import asyncio
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import yaml

from rag_api.settings import ontology
from rag_api.shared.config import Config
from rag_api.shared.db import BM25_DDL
from rag_api.shared.embedding import OpenAIEmbeddingClient, embed_text
from rag_api.shared.tokenize import current_dict_version, tsv_source
from rag_api.shared.version_header import FRONTMATTER, render_header

BATCH = 32          # 계약 3.4 파이프라인 묶음
EMBED_TIMEOUT_S = 30  # 계약 3.4 파이프라인 시간 제한(서버 질의의 EMBED_TIMEOUT_S 와 다름)


def load_yaml(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_body(path: Path) -> str:
    """정제 원문 파일의 본문. 경계 계약 5.2 처럼 텍스트 모드(줄바꿈 LF)로 읽고 머리말을 떼고 앞뒤 공백을 자름."""
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER.match(text)
    return (m[2] if m else text).strip()


def read_bodies(root: Path, docs: list[dict]) -> list[str]:
    """판마다 documents.yaml 의 relative_path(root 기준)로 정제 원문 본문을 읽음. 없는 파일이 있으면 FileNotFoundError."""
    return [read_body(root / d["relative_path"]) for d in docs]


def render_raw(row: dict, body: str) -> str:
    """`document_versions.raw_markdown`: 5.1 모양 머리말 + 본문. 머리말 값은 documents.yaml 의 그 버전 행(이전 판은 그 판의 allowed_depts).
    개발용이라 신뢰도·검수 사유는 고정값(수동 머리말 1.0, 없음)."""
    meta = {**row, "source_org": row.get("source_org") or None, "source_url": row.get("source_url") or None, "review_reason": None,
            "confidence_score": 1.0 if row["classification_method"] == "manual_frontmatter" else None}
    return render_header(meta) + body


async def create_db(dsn: str, reset: bool) -> None:
    url = urlsplit(dsn)
    name = url.path.lstrip("/")
    admin = await asyncpg.connect(url._replace(path="/postgres").geturl())
    try:
        if await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            if not reset:
                sys.exit(f"DB {name} 가 이미 있습니다. 다시 만들려면 --reset")
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def main(reset: bool) -> None:
    cfg = Config.from_env()
    data = Path(os.environ["RAG_DATA_DIR"])
    docs = load_yaml(data / "documents.yaml")
    # 판마다 documents.yaml 의 relative_path(rag_ready 의 부모 기준)로 원문을 읽음. 없으면 DB 에 붙기 전에 FileNotFoundError 로 멈춤
    bodies = read_bodies(data.parent, docs)
    chunks = load_yaml(data / "chunks.yaml") + load_yaml(data / "chunks_archived.yaml")
    t0 = time.monotonic()

    await create_db(cfg.database_url, reset)
    conn = await asyncpg.connect(cfg.database_url)
    try:
        await conn.execute(Path(os.environ["RAG_INIT_SQL"]).read_text(encoding="utf-8"))
        await conn.execute(BM25_DDL)
        await ontology.reload_if_changed(conn)  # tsv 를 init.sql 온톨로지 사전으로 자름

        # 임베딩: 정제본 embed_text 와 같은 shared.embed_text(끝 줄바꿈은 뗌, 계약 5.3)
        client = OpenAIEmbeddingClient(cfg.embed_base_url, cfg.embed_model, EMBED_TIMEOUT_S, cfg.embed_api_key)
        texts = [embed_text(c["title"], c.get("chapter_id"), c.get("chapter_name"), c["section_path"],
                            c["content"].rstrip("\n")) for c in chunks]
        t1 = time.monotonic()
        vectors = [v for i in range(0, len(texts), BATCH) for v in client.embed_passages(texts[i:i + BATCH])]
        embed_s = time.monotonic() - t1

        async with conn.transaction():
            head = {}  # 문서 행은 현행 버전(없으면 첫 행)의 머리말로
            for d in sorted(docs, key=lambda d: d["version_status"] != "active"):
                head.setdefault(d["doc_id"], d)
            await conn.executemany(
                "INSERT INTO documents (doc_id, title, doc_type, source_tier, knowledge_pack, dept_owner, allowed_depts,"
                " classification_method, needs_admin_review, is_discoverable, sync_quarter, ontology_graph_meta)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)",
                [(d["doc_id"], d["title"], d["doc_type"], d["source_tier"], d["knowledge_pack"], d["dept_owner"],
                  d["allowed_depts"], d["classification_method"], d["needs_admin_review"], d["is_discoverable"],
                  d["sync_quarter"], d["ontology_graph_meta"] or "{}") for d in head.values()])
            version_ids = {}
            for d, body in zip(docs, bodies):
                version_ids[(d["doc_id"], d["version_tag"])] = await conn.fetchval(
                    "INSERT INTO document_versions (doc_id, version_tag, sync_quarter, raw_markdown, content_hash,"
                    " version_status, effective_from, effective_to, source_org, source_url)"
                    " VALUES ($1, $2, $3, $10, $4, $5, $6, $7, $8, $9) RETURNING version_id",
                    d["doc_id"], d["version_tag"], d["sync_quarter"], d["content_hash"], d["version_status"],
                    date.fromisoformat(d["effective_from"]),
                    date.fromisoformat(d["effective_to"]) if d["effective_to"] else None,
                    d["source_org"] or None, d["source_url"] or None, render_raw(d, body))
            dict_version = current_dict_version()
            await conn.executemany(
                "INSERT INTO chunks (chunk_id, doc_id, version_id, chunk_index, section_path, content, tsv, embedding,"
                " applied_dict_version) VALUES ($1, $2, $3, $4, $5, $6, to_tsvector('simple', $7), $8::halfvec, $9)",
                [(c["chunk_id"], c["doc_id"], version_ids[(c["doc_id"], c["version_tag"])],
                  int(c["chunk_id"].rsplit("::c", 1)[1]), c["section_path"], c["content"].rstrip("\n"),
                  tsv_source(c["title"], c["section_path"], c["content"].rstrip("\n")), str(v), dict_version)
                 for c, v in zip(chunks, vectors)])
            await conn.executemany(
                "UPDATE documents SET current_version_id = $1 WHERE doc_id = $2",
                [(version_ids[(d["doc_id"], d["version_tag"])], d["doc_id"]) for d in docs if d["version_status"] == "active"])

        counts = await conn.fetchrow(
            "SELECT (SELECT count(*) FROM documents) AS documents, (SELECT count(*) FROM document_versions) AS versions,"
            " (SELECT count(*) FROM chunks) AS chunks,"
            " (SELECT count(*) FROM chunks c JOIN documents d ON d.current_version_id = c.version_id) AS current_chunks")
    finally:
        await conn.close()
    print(dict(counts), f"임베딩 {embed_s:.1f}초({len(texts) / embed_s:.1f}건/초), 전체 {time.monotonic() - t0:.1f}초",
          f"사전 {dict_version}")


if __name__ == "__main__":
    asyncio.run(main("--reset" in sys.argv[1:]))
