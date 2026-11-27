"""채널·그래프·존재 공개 SQL. 열람 조각은 acl.py 에서 받습니다.
검색 채널은 `{where}`(acl_predicate) 안에서 상위 K 개를 뽑습니다(뽑은 뒤 거르지 않음).
값 자리 이름은 SQL 안의 글자(JSON 키 등)와 겹치지 않게 짓습니다(`render` 는 따옴표 안도 바꿈)."""
import json
from datetime import date

from rag_api.knowledge_engine.acl import Sql, render
from rag_api.shared.current_version import current_predicate

# 지난 실측과 같은 모양(계획 3.4 4번). `<@>` 는 BM25 의 음수라 오름차순이 잘 맞는 순서
KEYWORD_SQL = """
SELECT c.chunk_id, c.section_path, c.content, tsvector_to_array(c.tsv) AS lexemes,
       d.doc_id, d.title, v.version_id, v.version_tag, d.doc_type, d.source_tier, d.dept_owner, v.effective_from,
       -(tsv_to_text(c.tsv) <@> to_bm25query({q}, 'idx_chunks_bm25')) AS bm25
FROM chunks c JOIN document_versions v ON v.version_id = c.version_id
              JOIN documents d ON d.doc_id = v.doc_id
WHERE {where}
  AND c.tsv @@ replace(plainto_tsquery('simple', {q})::text, ' & ', ' | ')::tsquery
ORDER BY tsv_to_text(c.tsv) <@> to_bm25query({q}, 'idx_chunks_bm25')
LIMIT {k}
"""

VECTOR_SQL = """
SELECT c.chunk_id, c.section_path, c.content, tsvector_to_array(c.tsv) AS lexemes,
       d.doc_id, d.title, v.version_id, v.version_tag, d.doc_type, d.source_tier, d.dept_owner, v.effective_from,
       c.embedding <=> {vec}::halfvec AS distance
FROM chunks c JOIN document_versions v ON v.version_id = c.version_id
              JOIN documents d ON d.doc_id = v.doc_id
WHERE {where}
ORDER BY c.embedding <=> {vec}::halfvec
LIMIT {k}
"""

# 문서마다 대표 청크 하나: 키워드가 가장 잘 맞는 청크, 없으면 첫 청크. 결과는 doc_id 순(DISTINCT ON)
# 토큰이 하나도 맞지 않으면 `<@>` 가 0 이라 bm25 는 NULL(키워드 점수 없음)
REPRESENTATIVE_SQL = """
SELECT DISTINCT ON (d.doc_id)
       c.chunk_id, c.section_path, c.content, tsvector_to_array(c.tsv) AS lexemes,
       d.doc_id, d.title, v.version_id, v.version_tag, d.doc_type, d.source_tier, d.dept_owner, v.effective_from,
       NULLIF(-(tsv_to_text(c.tsv) <@> to_bm25query({q}, 'idx_chunks_bm25')), 0) AS bm25
FROM chunks c JOIN document_versions v ON v.version_id = c.version_id
              JOIN documents d ON d.doc_id = v.doc_id
WHERE {where} AND d.doc_id = ANY({doc_ids}::text[])
ORDER BY d.doc_id, tsv_to_text(c.tsv) <@> to_bm25query({q}, 'idx_chunks_bm25'), c.chunk_index
"""

# 존재 공개 안내: 볼 수 없는 공개 표시 문서의 ID·제목·소관 부서명만. 본문·청크는 읽지 않음
DISCOVERABLE_SQL = """
SELECT d.doc_id, d.title, d.dept_owner, dep.default_name AS dept_name
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
                 JOIN departments dep ON dep.dept_code = d.dept_owner
WHERE {hidden} AND d.is_discoverable
"""

# 협업 그래프 출처 문서(요청 문서·그래프 채널의 상위 문서). `{where}` 는 acl_predicate
# 고른 문서 중 볼 수 있는 현행 문서(qa 5-13). `{where}` 에 doc_id 조건까지 들어 있음
VISIBLE_DOCS_SQL = """
SELECT d.doc_id
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
WHERE {where}
"""

GRAPH_META_SQL = """
SELECT d.doc_id, d.ontology_graph_meta::text AS meta
FROM documents d JOIN document_versions v ON v.version_id = d.current_version_id
WHERE {where} AND d.doc_id = ANY({doc_ids}::text[])
"""

# 그래프가 가리키는 문서. `{where}` 는 visible_predicate(현행 조건 없음): 볼 수 있는 폐기 문서도 남기고 is_active 를 다시 채움.
# `{current}` 는 현행 판 조건(종료일 지난 대상은 폐기로 보임. 포인터가 없으면 LEFT JOIN 의 NULL 이라 거짓)
GRAPH_TARGETS_SQL = """
SELECT d.doc_id, coalesce({current}, false) AS is_active, d.dept_owner
FROM documents d LEFT JOIN document_versions v ON v.version_id = d.current_version_id
WHERE {where} AND d.doc_id = ANY({target_ids}::text[])
"""


async def _fetch(pool, template: str, **params) -> list:
    sql, args = render(template, **params)
    return await pool.fetch(sql, *args)


async def keyword(pool, where: Sql, query: str, k: int) -> list:
    """`query` 는 토큰을 공백으로 이은 글."""
    # 같은 BM25 는 물리 순서로 나와 다시 적재하면 바뀜 → chunk_id 로 고정(SQL 에 둘째 키를 두면 BM25 인덱스 정렬을 못 탈 수 있음)
    # ponytail: LIMIT k 경계에 걸친 동점 중 무엇이 들어오는지는 여전히 물리 순서. 경계까지 고정하려면 k+여유로 받아 앱에서 자름
    return sorted(await _fetch(pool, KEYWORD_SQL, where=where, q=query, k=k), key=lambda r: (-r["bm25"], r["chunk_id"]))


async def vector(pool, where: Sql, vec: list[float], k: int) -> list:
    sql, args = render(VECTOR_SQL, where=where, vec=str(vec), k=k)
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute("SET LOCAL hnsw.iterative_scan = 'relaxed_order'")
        rows = await conn.fetch(sql, *args)
    return sorted(rows, key=lambda r: (r["distance"], r["chunk_id"]))  # relaxed_order 는 순서가 조금 어긋날 수 있음. 동점은 chunk_id(LIMIT 경계 동점은 키워드와 같은 한계)


async def representative(pool, where: Sql, doc_ids: list[str], query: str) -> list:
    return await _fetch(pool, REPRESENTATIVE_SQL, where=where, q=query, doc_ids=doc_ids)


async def discoverable(pool, hidden: Sql) -> list:
    return await _fetch(pool, DISCOVERABLE_SQL, hidden=hidden)


async def visible_docs(pool, where: Sql) -> set[str]:
    return {r["doc_id"] for r in await _fetch(pool, VISIBLE_DOCS_SQL, where=where)}


async def graph_meta(pool, where: Sql, doc_ids: list[str]) -> dict[str, dict]:
    """문서 ID → ontology_graph_meta(볼 수 있는 현행 문서만)."""
    return {r["doc_id"]: json.loads(r["meta"]) for r in await _fetch(pool, GRAPH_META_SQL, where=where, doc_ids=doc_ids)}


async def graph_targets(pool, visible: Sql, target_ids: list[str], today: date) -> tuple[dict[str, bool], dict[str, str]]:
    """볼 수 있는 문서만: (문서 ID → 현행 포인터가 active 버전을 가리키는지, 문서 ID → 소관 부서).
    볼 수 없거나 없는 문서는 빠짐."""
    rows = await _fetch(pool, GRAPH_TARGETS_SQL, current=current_predicate(today), where=visible, target_ids=target_ids)
    return {r["doc_id"]: r["is_active"] for r in rows}, {r["doc_id"]: r["dept_owner"] for r in rows}
