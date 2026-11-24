"""문서·용어 바로 찾기 `search.documents`(search.md, 약속서 03A, 검색 API 계획 3.4).

`knowledge_engine.retrieve` 결과를 문서 단위로 묶어 03A 응답으로 정리합니다. 생성 LLM 클라이언트를 import 하지 않습니다.
"""
import dataclasses
from datetime import date, datetime

from rag_api.auth.context import AuthContext
from rag_api.contracts.common import KST, ApiError, Status, discoverable_notices, version_badge
from rag_api.contracts.search import ConceptChip, DocumentHit, SearchRequest, SearchResponse, Snippet, TermCard
from rag_api.knowledge_engine.acl import active_packs
from rag_api.knowledge_engine.engine import KnowledgeEngine
from rag_api.knowledge_engine.types import DocMeta, RetrievalQuery, RetrievedChunk
from rag_api.rules.values import Rules
from rag_api.settings import ontology
from rag_api.shared.tokenize import _normalize

TEMPLATE, CASE = "standard_template", "benchmark_reference"  # 사규가 아닌 참고 자료: documents·total 이 아니라 따로(reference_templates·reference_cases)


def _decode_cursor(cursor: str | None) -> int:
    """documents 의 시작 위치(숫자 문자열). 없으면 0(첫 쪽). isdigit 은 "²" 도 통과시켜 isdecimal 로 봄."""
    if cursor is None:
        return 0
    offset = int(cursor) if cursor.isdecimal() else 0
    if offset < 1:
        raise ApiError(422, "VALIDATION_FAILED", "cursor 가 올바르지 않습니다.")
    return offset


def group(chunks: list[RetrievedChunk], departments: dict[str, str], snippets_per_doc: int, snippet_chars: int,
          today: date) -> list[DocumentHit]:
    """청크 순위대로 문서 단위로 묶음. 문서 순서는 그 문서의 첫 청크 순위, 발췌는 문서마다 앞에서부터."""
    grouped: dict[str, tuple[DocMeta, list[Snippet]]] = {}
    for c in chunks:
        meta, snippets = grouped.setdefault(c.doc.doc_id, (c.doc, []))
        if len(snippets) < snippets_per_doc:
            snippets.append(Snippet(chunk_id=c.chunk_id, section_path=c.section_path, text=c.content[:snippet_chars]))
    return [DocumentHit(**dataclasses.asdict(m), dept_name=departments.get(m.dept_owner, m.dept_owner),
                        version_badge=version_badge(m.effective_from, today), snippets=s)
            for m, s in grouped.values()]


def term_cards(query: str, ctx: AuthContext, packs: list[str]) -> list[TermCard]:
    """정규화한 검색어 전체가 표준 용어·동의어 하나와 같을 때만(띄어쓰기는 보지 않음). 요청자 부서 해설 먼저,
    그다음 전사. 비로그인·승인 전은 부서가 없어 전사 먼저(search.md §4)."""
    norm = lambda s: "".join(_normalize(s).split())
    q = norm(query)
    cards = []
    for c in ontology.current().concepts:
        hit = next((t for t in (c.canonical_term, *c.synonyms) if norm(t) == q), None)
        if hit and c.knowledge_pack in packs:
            cards.append(TermCard(concept_id=c.concept_id, canonical_term=c.canonical_term, definition=c.description,
                                  matched_text=hit, dept_owner=c.dept_owner))
    mine = {ctx.dept_code, *ctx.extra_depts} - {None} if ctx.status == "ACTIVE" else set()
    return sorted(cards, key=lambda t: (t.dept_owner not in mine, t.dept_owner not in (None, "COMMON"), t.concept_id))


async def search_documents(engine: KnowledgeEngine, departments: dict[str, str], rules: Rules, req: SearchRequest,
                           ctx: AuthContext, trace_id: str, today: date | None = None) -> SearchResponse:
    cfg = engine.cfg
    offset = _decode_cursor(req.cursor)
    first = offset == 0
    # 표준 양식·외부 참고 사례는 doc_types 와 관계없이 따로 오므로 유형 필터에 늘 더함
    # (외부 참고 사례는 include_benchmark 가 아니면 팩 조건에서 걸러져 결과가 늘지 않음)
    doc_types = tuple(dict.fromkeys([*req.doc_types, TEMPLATE, CASE])) if req.doc_types else ()
    # ponytail: 쪽마다 다시 찾음. 결과 상위 N(RESULT_TOP_N) 이 그대로라 순서가 같음. 상위 N 밖으로는 쪽을 넘기지 못함
    res = await engine.retrieve(RetrievalQuery(req.query, "hybrid", tuple(req.dept_filter), doc_types,
                                               req.include_benchmark), ctx)
    hits = group(res.chunks, departments, cfg.snippets_per_doc, cfg.snippet_chars, today or datetime.now(KST).date())
    documents = [h for h in hits if h.doc_type not in (TEMPLATE, CASE)]
    # 존재 공개 안내는 결과 건수와 관계없이 첫 쪽에(7차 회신 2.3절). 대상 판정(ACTIVE 비ADMIN)·볼 수 없는 문서만은 엔진이 함
    notices = discoverable_notices(await engine.discoverable_hints(req.query, ctx), departments,
                                   rules.cooperation_request) if first else []
    end = offset + req.limit
    return SearchResponse(
        total=len(documents), active_pack=ctx.active_pack, documents=documents[offset:end],
        reference_templates=[h for h in hits if h.doc_type == TEMPLATE][:cfg.reference_templates_max] if first else [],
        reference_cases=[h for h in hits if h.doc_type == CASE][:cfg.reference_templates_max] if first and req.include_benchmark else [],  # ponytail: 상한은 양식과 같은 설정을 같이 씀(따로 필요하면 설정을 나눔)
        term_cards=term_cards(req.query, ctx, active_packs(ctx, req.include_benchmark)) if first else [],
        concepts=[ConceptChip(concept_id=c.concept_id, canonical_term=c.canonical_term)
                  for c in res.matched_concepts] if first else [],
        notices=notices, next_cursor=str(end) if end < len(documents) else None,
        status=Status(degraded=res.status.degraded, reason_codes=list(res.status.reason_codes)), trace_id=trace_id)
