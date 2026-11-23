"""찾기 `retrieve`·존재 공개 안내·협업 그래프(설계 §3·§4·§5, 계획 3.4~3.6)."""
import asyncio
import logging
from functools import lru_cache

from rag_api.auth.context import AuthContext
from rag_api.knowledge_engine import channels
from rag_api.knowledge_engine.acl import Sql, acl_predicate, active_packs, hidden_predicate, visible_predicate
from rag_api.knowledge_engine.types import (DiscoverHint, DocMeta, GraphItem, KeywordToken, MatchedConcept,
                                            RetrievalQuery, RetrievalResult, RetrievedChunk, Status, Trace)
from rag_api.settings import ontology
from rag_api.settings.ontology import Concept
from rag_api.shared.config import Config
from rag_api.shared.tokenize import current_dict_version, tokenize_ko

log = logging.getLogger(__name__)


@lru_cache(maxsize=4096)
def _term_tokens(term: str, fingerprint: str | None) -> tuple[str, ...]:
    """사전 용어의 토큰. 사전 지문(판·행 수)이 바뀌면 다시 자름."""
    return tuple(tokenize_ko(term))


def expand(tokens: list[str], concepts: list[Concept], fingerprint: str | None, enabled: bool, cap: int
           ) -> tuple[list[Concept], list[KeywordToken], list[str]]:
    """맞은 개념, 키워드 채널 토큰(출처 포함), 대표 사규 후보 문서.

    개념은 대표어·동의어 중 하나의 토큰이 모두 질의 토큰에 있으면 맞은 것입니다.
    동의어 확장과 대표 사규는 `enabled` 일 때만(근거 부족 기준값을 확장 없이 쟀음, 계획 3.4 2번).
    """
    qset = set(tokens)
    matched = [c for c in concepts
               if any((tt := _term_tokens(t, fingerprint)) and set(tt) <= qset for t in (c.canonical_term, *c.synonyms))]
    kw = [KeywordToken(t, "query") for t in tokens]
    related: list[str] = []
    if enabled:
        seen = set(tokens)
        for c in matched:
            for i, term in enumerate((c.canonical_term, *c.synonyms)):
                for t in _term_tokens(term, fingerprint):
                    if t not in seen and len(kw) - len(tokens) < cap:
                        seen.add(t)
                        kw.append(KeywordToken(t, "canonical" if i == 0 else "synonym", c.concept_id))
            related += [d for d in c.related_doc_ids if d not in related]
    return matched, kw, related


def filter_graph(source_doc_id: str, meta: dict, visible: dict[str, bool], owners: dict[str, str]) -> dict:
    """`visible`(볼 수 있는 문서 → 현행 여부)으로 그래프를 거릅니다(설계 §5). 항목마다 source_doc_id 를 더하고
    충돌 항목에는 상대의 소관 부서(`owners`)를 target_dept_owner 로 더합니다(qa R2 기준 부서 판정, 응답에는 안 냄).
    그 밖의 필드는 그대로입니다.
    - 단계: 볼 수 없는 문서는 뺌. 볼 수 있는 폐기 문서는 남기고 is_active 를 다시 채움.
    - 서식(required_form_id)·충돌: 가리키는 문서가 볼 수 있는 현행일 때만 남김(서식은 키만 뺌).
      둘 다 is_active 가 없어 폐기 문서가 표시 없이 열리거나 병기되기 때문"""
    out = dict(meta)
    for key in ("prev_steps", "next_steps"):
        if key in meta:
            out[key] = [{**{k: v for k, v in s.items() if k != "required_form_id" or visible.get(v) is True},
                         "is_active": visible[s["doc_id"]], "source_doc_id": source_doc_id}
                        for s in meta[key] if s.get("doc_id") in visible]
    if "conflicts" in meta:
        out["conflicts"] = [{**c, "source_doc_id": source_doc_id, "target_dept_owner": owners[c["target_doc_id"]]}
                            for c in meta["conflicts"] if visible.get(c.get("target_doc_id")) is True]
    return out


def _steps(meta: dict) -> list[dict]:
    return [s for key in ("prev_steps", "next_steps") for s in meta.get(key, [])]


def _step_doc_ids(meta: dict) -> list[str]:
    return [s["doc_id"] for s in _steps(meta) if s.get("doc_id")]


def _referenced_ids(meta: dict) -> set[str]:
    """그래프가 가리키는 모든 문서: 단계·서식·충돌 상대."""
    ids = [*(s.get("doc_id") for s in _steps(meta)), *(s.get("required_form_id") for s in _steps(meta)),
           *(c.get("target_doc_id") for c in meta.get("conflicts", []))]
    return {i for i in ids if i}


def fuse(keyword_rows: list, vector_rows: list, kw_tokens: set[str], cfg: Config, graph_rows: list | None = None
         ) -> list[RetrievedChunk]:
    """RRF 로 합치고 같은 청크는 하나로, 점수가 같으면 공식 사규를 앞에 두어 상위 N 개."""
    found: dict[str, RetrievedChunk] = {}

    def get(r) -> RetrievedChunk:
        if r["chunk_id"] not in found:
            doc = DocMeta(r["doc_id"], r["title"], r["version_tag"], r["doc_type"], r["source_tier"],
                          r["dept_owner"], r["effective_from"], r["version_id"])
            found[r["chunk_id"]] = RetrievedChunk(r["chunk_id"], r["section_path"], r["content"], list(r["lexemes"]),
                                                  doc, matched_tokens=len(kw_tokens & set(r["lexemes"])))
        return found[r["chunk_id"]]

    for rank, r in enumerate(keyword_rows, 1):
        ch = get(r)
        ch.bm25, ch.keyword_rank = r["bm25"], rank
        ch.rrf += cfg.rrf_weight_keyword / (cfg.rrf_k + rank)
    for rank, r in enumerate(vector_rows, 1):
        ch = get(r)
        ch.cosine = 1 - r["distance"]
        ch.rrf += cfg.rrf_weight_vector / (cfg.rrf_k + rank)
    for rank, r in enumerate(graph_rows or [], 1):
        ch = get(r)
        ch.graph_rank = rank
        ch.rrf += cfg.rrf_weight_graph / (cfg.rrf_k + rank)
    # 05 근접성 보정·06 재순위 자리: 둘 다 꺼짐(켜면 Config 가 시작을 막음). 순서를 그대로 넘김
    for ch in found.values():
        ch.final = ch.rrf  # 개념 일치 가산점 자리(값 대기, F13)
    # 07 공식 사규 우선(2차 R1 → 7차 회신 1절: 동점일 때만): 곱하지 않고 RRF 가 같을 때만 source_tier 1 을 앞에.
    # benchmark 는 우선순위가 아니라 기본 검색에서 뺌
    # ponytail: float 덧셈 순서로 생기는 1 ulp 차이를 동점으로 봄. RRF_K·RRF_WEIGHT_* 를 바꾸면 값 사이 최소 간격을 다시 잼
    return sorted(found.values(), key=lambda c: (-round(c.final, 12), c.doc.source_tier != 1, c.chunk_id))[: cfg.result_top_n]


class KnowledgeEngine:
    def __init__(self, pool, embedder, cfg: Config):
        self.pool, self.embedder, self.cfg = pool, embedder, cfg

    async def retrieve(self, q: RetrievalQuery, ctx: AuthContext) -> RetrievalResult:
        cfg = self.cfg
        packs = active_packs(ctx, q.include_benchmark)
        where = acl_predicate(ctx, packs)
        if q.dept_filter:  # 필터는 열람 조건 위에 더함(범위를 넓히지 못함)
            where += Sql(" AND d.dept_owner = ANY($?::text[])", (list(q.dept_filter),))
        if q.doc_types:
            where += Sql(" AND d.doc_type = ANY($?::text[])", (list(q.doc_types),))
        if q.doc_ids:  # 모든 채널(키워드·벡터·그래프·대표 청크)이 이 조건을 써서 범위를 넓히지 못함
            where += Sql(" AND d.doc_id = ANY($?::text[])", (list(q.doc_ids),))

        # 첫 await 전까지 토큰화·확장을 끝내 사전이 중간에 바뀌어도 한 판으로 처리
        dict_version = current_dict_version()
        dictionary = ontology.current()
        tokens = tokenize_ko(q.text)
        concepts = [c for c in dictionary.concepts if c.knowledge_pack in packs]
        matched, kw_tokens, related = expand(tokens, concepts, dictionary.fingerprint,
                                             cfg.ontology_expansion_enabled, cfg.ontology_expansion_max_tokens)
        query = " ".join(t.token for t in kw_tokens)

        keyword_rows, vector_rows = await asyncio.gather(self._keyword(where, query, related),
                                                         self._vector(q.text, where))
        status = Status() if vector_rows is not None else Status(True, ("EMBEDDING_UNAVAILABLE",))
        vector_rows = vector_rows or []
        kw_set = {t.token for t in kw_tokens}
        graph_rows: list = []
        if q.strategy == "graph_hybrid":  # 키워드·벡터 상위 문서의 앞뒤 단계를 셋째 목록으로
            seeds = list(dict.fromkeys(c.doc.doc_id for c in fuse(keyword_rows, vector_rows, kw_set, cfg)))
            graph_rows = await self._graph(where, seeds[: cfg.graph_seed_docs], query)
        chunks = fuse(keyword_rows, vector_rows, kw_set, cfg, graph_rows)
        trace = Trace(dict_version, tuple(kw_tokens), len(keyword_rows), len(vector_rows), len(graph_rows))
        concepts_out = [MatchedConcept(c.concept_id, c.canonical_term, c.synonyms, c.description) for c in matched]
        top1 = 1 - vector_rows[0]["distance"] if vector_rows else None  # channels.vector 가 거리순으로 줌
        return RetrievalResult(chunks, concepts_out, trace, status, top1)

    async def _keyword(self, where: Sql, query: str, related: list[str]) -> list:
        if not query:
            return []
        rows = list(await channels.keyword(self.pool, where, query, self.cfg.keyword_top_k))
        have = {r["doc_id"] for r in rows}
        missing = [d for d in related if d not in have]
        if missing:  # 대표 사규는 키워드 목록 끝(doc_id 순)에 붙여 후보에 넣기만 함(가산 없음)
            rows += await channels.representative(self.pool, where, missing, query)
        return rows

    async def _vector(self, text: str, where: Sql) -> list | None:
        """임베딩을 못 쓰면 None(키워드만, degraded)."""
        if not self.cfg.vector_channel_enabled:
            return None
        try:
            # ponytail: to_thread 는 기본 실행기를 같이 씀. 시간 초과 뒤에도 스레드는 HTTP 시간 제한까지 남음. 문제가 되면 임베딩 전용 실행기.
            vec = await asyncio.wait_for(asyncio.to_thread(self.embedder.embed_query, text), self.cfg.embed_timeout_s)
        except Exception as e:
            msg = str(e) or (f"EMBED_TIMEOUT_S {self.cfg.embed_timeout_s}초 초과" if isinstance(e, TimeoutError) else "메시지 없음")
            log.warning("임베딩 실패, 키워드 채널만 씀: %s: %s", type(e).__name__, msg)  # 요청마다 남아 traceback 은 뺌
            return None
        return await channels.vector(self.pool, where, vec, self.cfg.vector_top_k)

    async def _graph(self, where: Sql, seeds: list[str], query: str) -> list:
        """그래프 채널(설계 §4.2 5번): 상위 문서(씨앗) 자신과 그 앞뒤 단계 문서마다 대표 청크. 공용 조건(`where`)을
        통과한 문서만. 씨앗을 빼면 이웃이 씨앗을 밀어내 절차 질문이 아닌 질의의 순위가 크게 떨어짐."""
        metas = await channels.graph_meta(self.pool, where, seeds) if seeds else {}
        docs = list(dict.fromkeys([*seeds, *(d for seed in seeds for d in _step_doc_ids(metas.get(seed, {})))]))
        if not docs:
            return []
        rows = await channels.representative(self.pool, where, docs, query)
        order = {d: i for i, d in enumerate(docs)}  # 씨앗(키워드·벡터 순) 다음 이웃(씨앗 순, 앞 → 뒤 단계). 이미 넣은 문서는 건너뜀
        return sorted(rows, key=lambda r: order[r["doc_id"]])

    async def visible_doc_ids(self, doc_ids: list[str], ctx: AuthContext) -> list[str]:
        """고른 문서 중 요청자가 볼 수 있는 현행 문서(활성 팩, 벤치마크 제외). 순서는 받은 대로."""
        where = acl_predicate(ctx, active_packs(ctx, False)) + Sql(" AND d.doc_id = ANY($?::text[])", (doc_ids,))
        visible = await channels.visible_docs(self.pool, where)
        return [d for d in dict.fromkeys(doc_ids) if d in visible]

    async def hidden_doc_ids(self, doc_ids: list[str], ctx: AuthContext) -> list[str]:
        """고른 문서 중 현행·활성 팩인데 요청자가 볼 수 없는 문서(약속서 00 4.2 "볼 수 없는 문서", 감사 ACL_DENIED 대상).
        없는 ID·현행이 아닌 문서·다른 팩 문서는 빠짐. user·ACTIVE·비ADMIN 이 아니면 빈 목록. 순서는 받은 대로."""
        hidden = hidden_predicate(ctx, active_packs(ctx, False))
        if hidden is None:
            return []
        found = await channels.visible_docs(self.pool, hidden + Sql(" AND d.doc_id = ANY($?::text[])", (doc_ids,)))
        return [d for d in dict.fromkeys(doc_ids) if d in found]

    async def discoverable_hints(self, q: str, ctx: AuthContext) -> list[DiscoverHint]:
        """볼 수 없는 공개 표시 문서 중 제목이 질의와 겹치는 것(설계 §3). user·ACTIVE·비ADMIN 이 아니면 빈 목록."""
        packs = active_packs(ctx, False)
        hidden = hidden_predicate(ctx, packs)
        if hidden is None:
            return []
        dictionary = ontology.current()
        concepts = [c for c in dictionary.concepts if c.knowledge_pack in packs]
        # 제목 매칭은 스위치와 관계없이 늘 동의어로 넓힘(설계 §3, 6차 11번 연봉 ↔ 급여). ONTOLOGY_EXPANSION_ENABLED 는
        # R3 기준값을 확장 없이 잰 찾기(retrieve)를 지키는 것이고, 이 안내는 R3 판정에 쓰이지 않음
        _, kw, _ = expand(tokenize_ko(q), concepts, dictionary.fingerprint, True, self.cfg.ontology_expansion_max_tokens)
        qset = {t.token for t in kw}
        if not qset:
            return []
        hits = []
        for r in await channels.discoverable(self.pool, hidden):
            overlap = len(qset & set(_term_tokens(r["title"], dictionary.fingerprint)))  # 제목 토큰은 제목·사전 지문으로 캐시
            if overlap:
                hits.append((-overlap, r["doc_id"], DiscoverHint(r["doc_id"], r["title"], r["dept_name"], r["dept_owner"])))
        return [h[2] for h in sorted(hits)][: self.cfg.discover_hint_limit]

    async def procedure_graph(self, doc_ids: list[str], ctx: AuthContext) -> list[GraphItem]:
        """요청 문서(볼 수 있는 현행 문서만)의 협업 그래프(설계 §5). 가리키는 문서는 한 번에 조회."""
        packs = active_packs(ctx, False)
        metas = await channels.graph_meta(self.pool, acl_predicate(ctx, packs), doc_ids)
        targets = set().union(*map(_referenced_ids, metas.values()))
        visible, owners = (await channels.graph_targets(self.pool, visible_predicate(ctx, packs), sorted(targets))
                           if targets else ({}, {}))
        return [GraphItem(d, filter_graph(d, metas[d], visible, owners)) for d in doc_ids if metas.get(d)]
